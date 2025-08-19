import json
import time
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import ast
import re

from executors import run_bash_script, run_python_script, str_replace_editor, replace_lines, run_javascript

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash script and return combined stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": { "type": "string" }
                },
                "required": ["script"]}}},
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute a Python script and return combined stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": { "type": "string" }
                },
                "required": ["script"]}}},
    {
        "type": "function",
        "function": {
            "name": "popContext",
            "description": """
                Used to return results to the calling agent which spawned you with spawn_agent or spawn_agent_auto if such exists.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "return_value": { "type": "string" }
                },
                "required": ["return_value"]}}},
    {
        "type": "function",
        "function": {
            "name": "str_replace_editor",
            "description": "Replace specific text in files (exact literal match, including whitespace).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"}
                },
                "required": ["file_path", "old_str", "new_str"]}}},
    {
        "type": "function",
        "function": {
            "name": "replace_lines",
            "description": """
                Simple, predictable line-number editing:
                action=replace|insert|delete.
                    replace: inclusive [start..end].
                    insert: before|after
                    start (use start=1,before for beginning; start=N+1,after for append).
                    delete: inclusive [start..end]. new_content lines get trailing newlines.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "new_content": {"type": "string"},
                    "action": {"type": "string", "enum": ["replace", "insert", "delete"]},
                    "position": {"type": "string", "enum": ["before", "after"]}},
                "required": ["file_path", "start_line", "new_content"]}}},
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": """
                Spawn a single child agent using current CWD as working dir.
                Please do not specify model_key unless user requested.
                Returns {tree_id,parent_id,child_id,dir,session}.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "context_text": {"type": "string"},
                    "label": {"type": "string"},
                    "model_key": {"type": "string"}
                },
                "required": ["context_text"]}}},
    {
        "type": "function",
        "function": {
            "name": "spawn_agent_auto",
            "description": """
                Spawn a single child agent using current CWD as working dir with auto-approval for tool calls (EG_YES_TOOL_FLAG=1).
                Please do not specify model_key unless user requested.
                Returns {tree_id,parent_id,child_id,dir,session}.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "context_text": {"type": "string"},
                    "label": {"type": "string"},
                    "model_key": {"type": "string"}
                },
                "required": ["context_text"]}}},
    {
        "type": "function",
        "function": {
            "name": "wait_agents",
            "description": """
                Wait for specific child agent IDs (e.g., label-001) to finish.
                Use IDs exactly as shown by /tree.
                Pass [] to wait for all.
                Optional timeout_sec.
                Set any_mode=true to return when any completes.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "which": {"type": "array","items": {"type": "string"}},
                    "timeout_sec": {"type": "integer"},
                    "any_mode": {"type": "boolean"}},
                "required": ["which"]}}},
    {
        "type": "function",
        "function": {
            "name": "javascript",
            "description": """
                Execute a javascript script in a browser remote debug mode.
                Searches for tab with the url if url provided. If it doesn't find it, it opens a new tab with and visits the url.
                To get the result of the script execution, you have to explicitly "return" it from javascript:
                    function extract() {
                      out = "something calculated here..."
                      //...
                      return out;
                    }
                    return extract();
                This is because the tool already wraps the script in a function.
            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "script": { "type": "string"},
                    "url": {"type": "string"}
                },
                "required": ["script"]
            }
        }
    }
]


AGENTS_BASE = Path.cwd() / '.egg' / 'agents'


def _tmux_raw(cmd: str) -> str:
    try:
        res = subprocess.run(cmd, shell=True, executable="/bin/bash", capture_output=True, text=True, timeout=10)
        return (res.stdout or "").strip()
    except Exception:
        return ""


def _ensure_session(tree_id: str) -> str:
    session = f"egg-tree-{tree_id}"
    run_bash_script(f"tmux has-session -t {session} 2>/dev/null || tmux new-session -d -s {session} 'bash'")
    return session


def _write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def _read_json(path: Path) -> Any:
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception:
        return None


def _next_child_id(children_dir: Path, base: str) -> str:
    normalized_base = base.replace(" ", "_").replace("/", "_").replace("\\", "_")
    max_idx = 0
    if children_dir.exists():
        for d in children_dir.iterdir():
            if d.is_dir() and d.name.startswith(normalized_base + "-"):
                try:
                    idx = int(d.name.split('-')[-1])
                    max_idx = max(max_idx, idx)
                except ValueError:
                    continue
    return f"{normalized_base}-{max_idx+1:03d}"


# Utilities for pane/window targeting

def _window_of_pane(pane_id: str) -> str:
    return _tmux_raw(f"tmux display -p -t {pane_id} '#{{window_id}}'")


def _active_pane_in_window_id(window_id: str) -> str:
    out = _tmux_raw("tmux list-panes -a -F '#{window_id} #{pane_id} #{pane_active}'")
    if not out:
        return ""
    first = ""
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 3 and parts[0] == window_id:
            if not first:
                first = parts[1]
            if parts[2] == '1':
                return parts[1]
    return first


def _read_parent_pane_id(tree_id: str, parent_id: str) -> str:
    p = AGENTS_BASE / tree_id / parent_id / 'state.json'
    st = _read_json(p)
    if isinstance(st, dict):
        pid = st.get('pane_id')
        if isinstance(pid, str) and pid:
            return pid
    return ""


def _write_parent_right_column_pane(tree_id: str, parent_id: str, right_pane_id: str):
    p = AGENTS_BASE / tree_id / parent_id / 'state.json'
    st = _read_json(p) or {}
    st['right_column_pane_id'] = right_pane_id
    _write_json(p, st)


def _read_parent_right_column_pane(tree_id: str, parent_id: str) -> str:
    p = AGENTS_BASE / tree_id / parent_id / 'state.json'
    st = _read_json(p) or {}
    v = st.get('right_column_pane_id')
    return v if isinstance(v, str) else ""


def _write_child_pane_id(tree_id: str, parent_id: str, child_id: str, pane_id: str):
    p = AGENTS_BASE / tree_id / parent_id / 'children' / child_id / 'state.json'
    st = _read_json(p) or {}
    st['pane_id'] = pane_id
    _write_json(p, st)


def _pane_exists(pane_id: str) -> bool:
    if not pane_id:
        return False
    out = _tmux_raw("tmux list-panes -a -F '#{pane_id}' | grep -Fx %s || true" % pane_id)
    return pane_id in (out.split() if out else [])


def _split_h(target_pane: str) -> str:
    run_bash_script(f"tmux split-window -h -t {target_pane}")
    return _tmux_raw("tmux display-message -p '#{pane_id}'")


def _split_v(target_pane: str) -> str:
    run_bash_script(f"tmux split-window -v -t {target_pane}")
    run_bash_script(f"tmux select-layout -E")
    return _tmux_raw("tmux display-message -p '#{pane_id}'")


def _kill_pane(pane_id: str):
    if not pane_id:
        return
    run_bash_script(f"tmux kill-pane -t {pane_id} 2>/dev/null || true")


# Spawning logic per requirements

def _spawn_into_parent_layer(session: str, tree_id: str, parent_id: str, run_script: str) -> str:
    parent_pane = _read_parent_pane_id(tree_id, parent_id)
    if not parent_pane:
        parent_pane = _tmux_raw("tmux display-message -p '#{pane_id}'")
        if not parent_pane:
            out = _tmux_raw(f"tmux list-panes -t {session} -F '#{{pane_id}}' | head -n1")
            parent_pane = out.splitlines()[0].strip() if out else ""
    if not parent_pane:
        return ""

    right_col = _read_parent_right_column_pane(tree_id, parent_id)
    if not right_col or not _pane_exists(right_col):
        right_col = _split_h(parent_pane)
        _write_parent_right_column_pane(tree_id, parent_id, right_col)
        target_for_child = right_col
    else:
        target_for_child = _split_v(right_col)

    escaped_script = run_script.replace("'", "'\"'\"'")
    run_bash_script(f"tmux send-keys -t {target_for_child} '{escaped_script}' C-m")
    return target_for_child


def _launch_child(session: str, parent_cwd: str, agent_dir: str, child_id: str, tree_id: str, parent_id: str, extra_env: Optional[dict] = None):
    repo_root = Path(__file__).resolve().parent
    chat_sh = (repo_root / 'chat.sh').resolve()
    chat_py = (repo_root / 'chat.py').resolve()
    init_ctx = Path(agent_dir) / 'init_context.txt'

    run_sh_path = Path(agent_dir) / 'run.sh'
    run_lines = [
        "#!/usr/bin/env bash",
        "set -e",
        f"cd '{parent_cwd}'",
        f"export EG_AGENT_DIR='{agent_dir}'",
        f"export EG_TREE_ID='{tree_id}'",
        f"export EG_PARENT_ID='{parent_id}'",
        f"export EG_AGENT_ID='{child_id}'",
        f"export EG_INIT_CONTEXT_FILE='{init_ctx}'",
    ]

    if extra_env:
        for k, v in extra_env.items():
            run_lines.append(f"export {k}='{v}'")

    if chat_sh.exists():
        run_lines.append(f"exec \"{str(chat_sh)}\" --tree '{tree_id}' --inline")
    else:
        run_lines.append(f"exec python3 -u '{str(chat_py)}'")

    run_sh_path.write_text("\n".join(run_lines) + "\n", encoding='utf-8')
    os.chmod(run_sh_path, 0o755)

    run_cmd = f"'{run_sh_path}'"
    child_pane = _spawn_into_parent_layer(session, tree_id, parent_id, run_cmd)
    if child_pane:
        _write_child_pane_id(tree_id, parent_id, child_id, child_pane)


def tool_spawn_agent(args: Dict) -> str:
    context_text = args.get('context_text', '').strip()
    label = (args.get('label') or 'child').strip() or 'child'
    # Prefer explicit model key; if not provided, try parent state, then DEFAULT_MODEL env
    model_key = args.get('model_key')

    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        current = AGENTS_BASE / '.current_tree'
        if current.exists():
            try:
                tree_id = current.read_text().strip()
            except Exception:
                tree_id = None
    if not tree_id:
        tree_id = str(int(time.time()))
        (AGENTS_BASE).mkdir(parents=True, exist_ok=True)
        (AGENTS_BASE / '.current_tree').write_text(tree_id)

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    parent_cwd = str(Path.cwd())

    # If no model_key provided, attempt to read parent's state.json
    if not model_key:
        try:
            parent_state = _read_json(AGENTS_BASE / tree_id / parent_id / 'state.json')
            if isinstance(parent_state, dict):
                pmk = parent_state.get('model_key')
                if isinstance(pmk, str) and pmk:
                    model_key = pmk
        except Exception:
            model_key = model_key

    if not model_key:
        model_key = os.environ.get('DEFAULT_MODEL') or ''

    base_dir = AGENTS_BASE / tree_id / parent_id / 'children'
    base_dir.mkdir(parents=True, exist_ok=True)
    child_id = _next_child_id(base_dir, label)
    child_dir = base_dir / child_id
    child_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "agent_id": child_id,
        "parent_id": parent_id,
        "status": "active",
        "model_key": model_key,
        "spawned_at": int(time.time()),
        "children": [],
        "cwd": str(parent_cwd)
    }
    _write_json(child_dir / 'state.json', state)
    _write_json(child_dir / 'messages.json', [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": context_text}
    ])
    (child_dir / 'init_context.txt').write_text(context_text or '', encoding='utf-8')

    session = _ensure_session(tree_id)
    extra_env = None
    if model_key:
        # Export both EG_CHILD_MODEL (highest precedence in ChatClient) and DEFAULT_MODEL
        extra_env = {"EG_CHILD_MODEL": model_key, "DEFAULT_MODEL": model_key}
    _launch_child(session, parent_cwd, str(child_dir), child_id, tree_id, parent_id, extra_env=extra_env)

    return json.dumps({
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "dir": str(child_dir),
        "session": session
    }, indent=2)


def tool_wait_agents(args: Dict) -> str:
    which = args.get('which')
    timeout = int(args.get('timeout_sec', 0))
    any_mode = bool(args.get('any_mode', False))

    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        try:
            tree_id = (AGENTS_BASE / '.current_tree').read_text().strip()
        except Exception:
            tree_id = None
    if not tree_id:
        return json.dumps({"error": "No tree context found"})

    if not isinstance(which, list):
        return json.dumps({"error": "which must be a list (empty list means all children)"})

    all_children = _list_all_children_dirs(tree_id)
    name_to_dir = {name: p for name, p in all_children}

    if len(which) == 0:
        target_ids = [name for name, _ in all_children]
    else:
        target_ids = [str(x) for x in which]

    start = time.time()
    results: Dict[str, Any] = {}
    pending = set(target_ids)

    if not pending:
        return json.dumps({"completed": [], "results": {}, "pending": []}, indent=2)

    while pending:
        for cid in list(pending):
            cdir = name_to_dir.get(cid)
            if cdir and (cdir / 'result.json').exists():
                try:
                    results[cid] = _read_json(cdir / 'result.json')
                except Exception:
                    results[cid] = {"status": "done"}
                pending.remove(cid)
                if any_mode:
                    pending_list = list(pending)
                    st = _read_json(cdir / 'state.json') or {}
                    pane_id = st.get('pane_id', '') if isinstance(st, dict) else ''
                    if pane_id:
                        _kill_pane(pane_id)
                    return json.dumps({
                        "completed": list(results.keys()),
                        "results": results,
                        "pending": pending_list
                    }, indent=2)
        if not pending:
            break
        if timeout and (time.time() - start) > timeout:
            break
        all_children = _list_all_children_dirs(tree_id)
        name_to_dir = {name: p for name, p in all_children}
        time.sleep(1)

    for cid, res in list(results.items()):
        cdir = name_to_dir.get(cid)
        if not cdir:
            continue
        st = _read_json(cdir / 'state.json') or {}
        pane_id = st.get('pane_id', '') if isinstance(st, dict) else ''
        if pane_id:
            _kill_pane(pane_id)

    return json.dumps({
        "completed": list(results.keys()),
        "results": results,
        "pending": list(pending)
    }, indent=2)


def _list_all_children_dirs(tree_id: str) -> List[Tuple[str, Path]]:
    base = AGENTS_BASE / tree_id
    out: List[Tuple[str, Path]] = []
    if not base.exists():
        return out
    for parent_dir in base.iterdir():
        if not parent_dir.is_dir():
            continue
        children_root = parent_dir / 'children'
        if not children_root.exists():
            continue
        for c in children_root.iterdir():
            if c.is_dir():
                out.append((c.name, c))
    return out


def parse_tool_calls_from_content(message_content: str) -> list:
    tool_calls = []
    if not message_content or not message_content.strip():
        return tool_calls
    try:
        parsed = json.loads(message_content.strip())
        if isinstance(parsed, dict) and 'tool_calls' in parsed:
            tcs = parsed.get('tool_calls')
            if isinstance(tcs, list):
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get('function', {})
                    name = fn.get('name')
                    args = fn.get('arguments', {})
                    if name:
                        args_str = args if isinstance(args, str) else json.dumps(args)
                        tool_calls.append({"type": "function", "function": {"name": name, "arguments": args_str}})
        elif isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                name = item.get('name')
                args = item.get('arguments', {})
                if name:
                    args_str = args if isinstance(args, str) else json.dumps(args)
                    tool_calls.append({"type": "function", "function": {"name": name, "arguments": args_str}})
        elif isinstance(parsed, dict):
            name = parsed.get('name')
            args = parsed.get('arguments', {})
            if name:
                args_str = args if isinstance(args, str) else json.dumps(args)
                tool_calls.append({"type": "function", "function": {"name": name, "arguments": args_str}})
    except json.JSONDecodeError:
        pass
    return tool_calls


def handle_tool_call(client, call: Dict, display_call: bool = True):
    fn_name = call["function"]["name"]
    # Parse arguments robustly. Accept either a single JSON object, a Python-dict-like object, or
    # multiple JSON objects concatenated (as some providers stream multiple tool invocations without separators).
    args_raw = call["function"].get("arguments", "{}")

    def _split_json_objects(s: str):
        objs = []
        if not isinstance(s, str) or not s:
            return objs
        i = 0
        L = len(s)
        while True:
            # find next opening brace
            start = s.find('{', i)
            if start == -1:
                break
            depth = 0
            j = start
            while j < L:
                if s[j] == '{':
                    depth += 1
                elif s[j] == '}':
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            if depth == 0:
                objs.append(s[start:j])
                i = j
            else:
                break
        return objs

    parsed_args_list = []
    args_is_str = isinstance(args_raw, str)
    if args_is_str:
        # 1) Try direct JSON
        try:
            parsed = json.loads(args_raw or "{}")
            # If it's a list, flatten it
            if isinstance(parsed, list):
                parsed_args_list = parsed
            else:
                parsed_args_list = [parsed]
        except Exception:
            # 2) Try to repair common concatenated-JSON case by inserting commas between adjacent objects
            try:
                repaired = re.sub(r"}\s*{", ",{", args_raw.strip())
                wrapped = f"[{repaired}]"
                parsed = json.loads(wrapped)
                parsed_args_list = parsed if isinstance(parsed, list) else [parsed]
            except Exception:
                # 3) Try Python literal eval for Python-style dicts
                try:
                    parsed_py = ast.literal_eval(args_raw)
                    if isinstance(parsed_py, dict):
                        parsed_args_list = [parsed_py]
                    elif isinstance(parsed_py, (list, tuple)):
                        parsed_args_list = list(parsed_py)
                except Exception:
                    # 4) Fallback: brute-force split objects by brace matching
                    pieces = _split_json_objects(args_raw)
                    for p in pieces:
                        try:
                            parsed_args_list.append(json.loads(p))
                        except Exception:
                            # skip unparseable fragments
                            continue
    else:
        # args_raw already a dict-like
        parsed_args_list = [args_raw or {}]

    if not parsed_args_list:
        tool_msg = {"role": "tool", "name": fn_name, "tool_call_id": call.get("id"), "content": "Error: Invalid arguments."}
        client.messages.append(tool_msg)
        client.display_manager.render_message(tool_msg)
        return

    # Display the call(s)
    if display_call:
        try:
            if len(parsed_args_list) == 1:
                client.console.print(json.dumps({"tool": fn_name, "args": parsed_args_list[0]}, indent=2))
            else:
                client.console.print(json.dumps({"tool": fn_name, "args": parsed_args_list}, indent=2))
        except Exception:
            pass

    # Ask confirmation once for multiple invocations unless auto-approved
    execute = True if (client.in_single_turn_auto_execute_calls or client.yesToolFlag) else None
    if execute is None:
        while True:
            response = input(f"Execute the {fn_name} tool call(s)? [y/n/a] ").strip().lower()
            if response in ('y', 'n', 'a'):
                break
            print("Invalid input. Please enter y, n, or a")
        if response == 'a':
            client.in_single_turn_auto_execute_calls = True
            execute = True
        elif response == 'y':
            execute = True
        else:
            execute = False

    outputs = []
    if not execute:
        outputs = ["--- SKIPPED BY USER ---"] * len(parsed_args_list)
    else:
        # If there are multiple parsed arg objects, try to infer if the function name string actually
        # encodes multiple concatenated tool names (common when streaming merges names). If so, split
        # the function name into a sequence to run one-by-one.
        names_seq = [fn_name]
        if len(parsed_args_list) > 1:
            # Gather known tool names
            known_tools = [t["function"]["name"] for t in TOOLS]
            # Fast check: repeated same tool name
            for kt in known_tools:
                if kt * len(parsed_args_list) == fn_name:
                    names_seq = [kt] * len(parsed_args_list)
                    break
            else:
                # Greedy scan: match longest known tool names repeatedly
                sorted_known = sorted(known_tools, key=lambda x: -len(x))
                seq = []
                s = fn_name
                while s:
                    matched = False
                    for kt in sorted_known:
                        if s.startswith(kt):
                            seq.append(kt)
                            s = s[len(kt):]
                            matched = True
                            break
                    if not matched:
                        break
                if len(seq) == len(parsed_args_list):
                    names_seq = seq
        # Ensure names_seq length matches parsed args; attempt heuristics if mismatch
        if len(names_seq) != len(parsed_args_list):
            try:
                all_have_ctx = all(isinstance(a, dict) and 'context_text' in a for a in parsed_args_list)
            except Exception:
                all_have_ctx = False
            if all_have_ctx:
                chosen = 'spawn_agent_auto' if 'auto' in fn_name.lower() else 'spawn_agent'
                names_seq = [chosen] * len(parsed_args_list)
            else:
                names_seq = [fn_name] * len(parsed_args_list)

        for i, args in enumerate(parsed_args_list):
            cur_name = names_seq[i]
            # Inject current model if spawning and no explicit model was provided
            if cur_name in ("spawn_agent", "spawn_agent_auto"):
                try:
                    if not isinstance(args, dict):
                        args = {}
                        parsed_args_list[i] = args
                    if not args.get("model_key"):
                        mk = getattr(client, "current_model_key", "") or ""
                        if mk:
                            args["model_key"] = mk
                except Exception:
                    pass
            try:
                if cur_name == "bash":
                    out = run_bash_script(args.get("script", ""))
                elif cur_name == "python":
                    out = run_python_script(args.get("script", ""))
                elif cur_name == "javascript":
                    out = run_javascript(args)
                elif cur_name == "popContext":
                    out = client.pop_context(args.get("return_value", ""))
                elif cur_name == "str_replace_editor":
                    out = str_replace_editor(args.get("file_path"), args.get("old_str"), args.get("new_str"))
                elif cur_name == "replace_lines":
                    out = replace_lines(args.get("file_path"), args.get("start_line"), args.get("end_line"), args.get("new_content"))
                elif cur_name == "spawn_agent":
                    out = tool_spawn_agent(args)
                elif cur_name == "wait_agents":
                    try:
                        out = tool_wait_agents(args)
                    except KeyboardInterrupt:
                        out = json.dumps({"interrupted": True, "message": "wait_agents interrupted by user"}, indent=2)
                elif cur_name == "write_result":
                    out = tool_write_result(args)
                elif cur_name == "list_agents":
                    out = tool_list_agents(args)
                elif cur_name == "spawn_agent_auto":
                    out = tool_spawn_agent_auto(args)
                else:
                    out = f"Unknown tool: {cur_name}"
            except Exception as e:
                out = f"Error executing {cur_name}: {e}"
            outputs.append(out)

    # Aggregate outputs into a single tool message for display and transcript
    if len(outputs) == 1:
        final_output = outputs[0]
    else:
        final_output = "\n\n==== SPLIT RESULTS ===\n\n".join(outputs)

    tool_msg = {"role": "tool", "name": fn_name, "tool_call_id": call.get("id"), "content": final_output}
    client.messages.append(tool_msg)
    client.display_manager.render_message(tool_msg)


def tool_list_agents(args: Dict) -> str:
    tree_id = args.get('tree_id') or os.environ.get('EG_TREE_ID')
    if not tree_id:
        try:
            tree_id = (AGENTS_BASE / '.current_tree').read_text().strip()
        except Exception:
            tree_id = None
    if not tree_id:
        return json.dumps({"error": "No tree context found"})
    listing: Dict[str, List[Dict[str, Any]]] = {}
    base = AGENTS_BASE / tree_id
    if not base.exists():
        return json.dumps({"tree_id": tree_id, "parents": listing}, indent=2)
    for parent_dir in base.iterdir():
        if not parent_dir.is_dir():
            continue
        parent_id = parent_dir.name
        children_root = parent_dir / 'children'
        if not children_root.exists():
            continue
        children: List[Dict[str, Any]] = []
        for c in children_root.iterdir():
            if not c.is_dir():
                continue
            state = _read_json(c / 'state.json') or {}
            res = _read_json(c / 'result.json')
            status = "done" if isinstance(res, dict) else state.get("status", "active")
            rv = res.get("return_value") if isinstance(res, dict) else None
            children.append({
                "child_id": c.name,
                "status": status,
                "return_value": rv
            })
        if children:
            listing[parent_id] = children
    return json.dumps({"tree_id": tree_id, "parents": listing}, indent=2)



def tool_spawn_agent_auto(args: Dict) -> str:
    context_text = args.get('context_text', '').strip()
    label = (args.get('label') or 'child').strip() or 'child'
    model_key = args.get('model_key')

    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        current = AGENTS_BASE / '.current_tree'
        if current.exists():
            try:
                tree_id = current.read_text().strip()
            except Exception:
                tree_id = None
    if not tree_id:
        tree_id = str(int(time.time()))
        (AGENTS_BASE).mkdir(parents=True, exist_ok=True)
        (AGENTS_BASE / '.current_tree').write_text(tree_id)

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    parent_cwd = str(Path.cwd())

    # If no model_key provided, attempt to read parent's state.json
    if not model_key:
        try:
            parent_state = _read_json(AGENTS_BASE / tree_id / parent_id / 'state.json')
            if isinstance(parent_state, dict):
                pmk = parent_state.get('model_key')
                if isinstance(pmk, str) and pmk:
                    model_key = pmk
        except Exception:
            model_key = model_key

    if not model_key:
        model_key = os.environ.get('DEFAULT_MODEL') or ''

    base_dir = AGENTS_BASE / tree_id / parent_id / 'children'
    base_dir.mkdir(parents=True, exist_ok=True)
    child_id = _next_child_id(base_dir, label)
    child_dir = base_dir / child_id
    child_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "agent_id": child_id,
        "parent_id": parent_id,
        "status": "active",
        "model_key": model_key,
        "spawned_at": int(time.time()),
        "children": [],
        "cwd": str(parent_cwd)
    }
    _write_json(child_dir / 'state.json', state)
    _write_json(child_dir / 'messages.json', [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": context_text}
    ])
    (child_dir / 'init_context.txt').write_text(context_text or '', encoding='utf-8')

    session = _ensure_session(tree_id)
    extra_env = None
    if model_key:
        extra_env = {"EG_CHILD_MODEL": model_key, "DEFAULT_MODEL": model_key, "EG_YES_TOOL_FLAG": "1"}
    else:
        extra_env = {"EG_YES_TOOL_FLAG": "1"}
    _launch_child(session, parent_cwd, str(child_dir), child_id, tree_id, parent_id, extra_env=extra_env)

    return json.dumps({
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "dir": str(child_dir),
        "session": session
    }, indent=2)
