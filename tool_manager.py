import json
import time
import os
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

from executors import run_bash_script, run_python_script, str_replace_editor, replace_lines

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Execute a bash script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
    {"type": "function", "function": {"name": "python", "description": "Execute a Python script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
    {"type": "function", "function": {"name": "pushContext", "description": "Save current chat and start new context conversation.",
                                      "parameters": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}}},
    {"type": "function", "function": {"name": "popContext", "description": "Save current chat and restore previous context conversation.",
                                      "parameters": {"type": "object", "properties": {"return_value": {"type": "string"}}, "required": ["return_value"]}}},
    {"type": "function", "function": {
        "name": "str_replace_editor",
        "description": "Replace specific text in files (exact literal match, including whitespace).",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"}
            },
            "required": ["file_path", "old_str", "new_str"]
        }
    }},
    {"type": "function", "function": {
        "name": "replace_lines",
        "description": "Replaces a specified range of lines in a file with new content.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "new_content": {"type": "string"}
            },
            "required": ["file_path", "start_line", "end_line", "new_content"]
        }
    }},
    {"type": "function", "function": {
        "name": "spawn_agent",
        "description": "Spawn a single child agent using current CWD as working dir. Returns {tree_id,parent_id,child_id,dir,session}.",
        "parameters": {
            "type": "object",
            "properties": {
                "context_text": {"type": "string"},
                "label": {"type": "string"}
            },
            "required": ["context_text"]
        }
    }},
    {"type": "function", "function": {
        "name": "spawn_agent_auto",
        "description": "Spawn a single child agent using current CWD as working dir with auto-approval for tool calls (EG_YES_TOOL_FLAG=1). Returns {tree_id,parent_id,child_id,dir,session}.",
        "parameters": {
            "type": "object",
            "properties": {
                "context_text": {"type": "string"},
                "label": {"type": "string"}
            },
            "required": ["context_text"]
        }
    }},
    {"type": "function", "function": {
        "name": "wait_agents",
        "description": "Wait for specific child agent IDs (e.g., label-001) to finish. Use IDs exactly as shown by /tree. Pass [] to wait for all. Optional timeout_sec. Set any_mode=true to return when any completes.",
        "parameters": {
            "type": "object",
            "properties": {
                "which": {"type": "array", "items": {"type": "string"}},
                "timeout_sec": {"type": "integer"},
                "any_mode": {"type": "boolean"}
            },
            "required": ["which"]
        }
    }},
    {"type": "function", "function": {
        "name": "write_result",
        "description": "Write result.json and mark done for the current agent directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_dir": {"type": "string"},
                "return_value": {"type": "string"},
                "summary": {"type": "string"}
            },
            "required": ["agent_dir", "return_value"]
        }
    }},
    {"type": "function", "function": {
        "name": "list_agents",
        "description": "List all children of the current tree, grouped by parent, with status.",
        "parameters": {
            "type": "object",
            "properties": {
                "tree_id": {"type": "string"}
            }
        }
    }}
]


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
    # Normalize the base name to handle spaces and special characters
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
    p = Path('.egg/agents') / tree_id / parent_id / 'state.json'
    st = _read_json(p)
    if isinstance(st, dict):
        pid = st.get('pane_id')
        if isinstance(pid, str) and pid:
            return pid
    return ""


def _write_parent_right_column_pane(tree_id: str, parent_id: str, right_pane_id: str):
    p = Path('.egg/agents') / tree_id / parent_id / 'state.json'
    st = _read_json(p) or {}
    st['right_column_pane_id'] = right_pane_id
    _write_json(p, st)


def _read_parent_right_column_pane(tree_id: str, parent_id: str) -> str:
    p = Path('.egg/agents') / tree_id / parent_id / 'state.json'
    st = _read_json(p) or {}
    v = st.get('right_column_pane_id')
    return v if isinstance(v, str) else ""


def _write_child_pane_id(tree_id: str, parent_id: str, child_id: str, pane_id: str):
    p = Path('.egg/agents') / tree_id / parent_id / 'children' / child_id / 'state.json'
    st = _read_json(p) or {}
    st['pane_id'] = pane_id
    _write_json(p, st)


def _pane_exists(pane_id: str) -> bool:
    if not pane_id:
        return False
    out = _tmux_raw(f"tmux list-panes -a -F '#{{pane_id}}' | grep -Fx {pane_id} || true")
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
        # Try fallback: the active pane of the current tmux client
        parent_pane = _tmux_raw("tmux display-message -p '#{pane_id}'")
        if not parent_pane:
            # Last-ditch: try first pane in the first window of session
            out = _tmux_raw(f"tmux list-panes -t {session} -F '#{{pane_id}}' | head -n1")
            parent_pane = out.splitlines()[0].strip() if out else ""
    if not parent_pane:
        return ""

    # Check if a right column already exists for this parent and is alive
    right_col = _read_parent_right_column_pane(tree_id, parent_id)
    if not right_col or not _pane_exists(right_col):
        # First child in this layer OR previous right column was killed: create new right column
        right_col = _split_h(parent_pane)
        _write_parent_right_column_pane(tree_id, parent_id, right_col)
        target_for_child = right_col
    else:
        # Subsequent child: stack within right column by horizontal split
        target_for_child = _split_v(right_col)

    # Send child start command to the target pane
    # Properly escape the command for tmux send-keys
    escaped_script = run_script.replace("'", "'\"'\"'")
    run_bash_script(f"tmux send-keys -t {target_for_child} '{escaped_script}' C-m")
    return target_for_child


def _launch_child(session: str, parent_cwd: str, agent_dir: str, child_id: str, tree_id: str, parent_id: str, extra_env: Optional[dict] = None):
    repo_root = Path(__file__).resolve().parent
    chat_sh = (repo_root / 'chat.sh').resolve()
    chat_py = (repo_root / 'chat.py').resolve()
    init_ctx = Path(agent_dir) / 'init_context.txt'

    # Prepare a per-child run script to avoid quoting/whitespace issues in tmux send-keys
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

    # Optional extra environment (e.g., EG_YES_TOOL_FLAG=1 for spawn_auto)
    if extra_env:
        for k, v in extra_env.items():
            run_lines.append(f"export {k}='{v}'")

    if chat_sh.exists():
        run_lines.append(f"exec \"{str(chat_sh)}\" --tree '{tree_id}' --inline")
    else:
        run_lines.append(f"exec python3 -u '{str(chat_py)}'")

    run_sh_path.write_text("\n".join(run_lines) + "\n", encoding='utf-8')
    os.chmod(run_sh_path, 0o755)

    # Properly quote the command for tmux send-keys
    run_cmd = f"'{run_sh_path}'"

    # Spawn strictly by pane, never switching/creating windows
    child_pane = _spawn_into_parent_layer(session, tree_id, parent_id, run_cmd)

    # Persist the new child's pane id so it can act as parent later
    if child_pane:
        _write_child_pane_id(tree_id, parent_id, child_id, child_pane)


def tool_spawn_agent(args: Dict) -> str:
    context_text = args.get('context_text', '').strip()
    label = (args.get('label') or 'child').strip() or 'child'

    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        current = Path('.egg/agents/.current_tree')
        if current.exists():
            try:
                tree_id = current.read_text().strip()
            except Exception:
                tree_id = None
    if not tree_id:
        tree_id = str(int(time.time()))
        Path('.egg/agents').mkdir(parents=True, exist_ok=True)
        Path('.egg/agents/.current_tree').write_text(tree_id)

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    parent_cwd = str(Path.cwd())

    base_dir = Path('.egg/agents') / tree_id / parent_id / 'children'
    base_dir.mkdir(parents=True, exist_ok=True)
    child_id = _next_child_id(base_dir, label)
    child_dir = base_dir / child_id
    child_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "agent_id": child_id,
        "parent_id": parent_id,
        "status": "active",
        "model_key": "",
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
    _launch_child(session, parent_cwd, str(child_dir), child_id, tree_id, parent_id, extra_env=None)

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
            tree_id = Path('.egg/agents/.current_tree').read_text().strip()
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


def tool_write_result(args: Dict) -> str:
    agent_dir = args.get('agent_dir')
    return_value = args.get('return_value', '')
    summary = args.get('summary', '')
    if not agent_dir:
        return "Error: agent_dir is required"
    p = Path(agent_dir)
    res = {
        "status": "done",
        "return_value": return_value,
        "summary": summary,
        "finished_at": int(time.time())
    }
    _write_json(p / 'result.json', res)
    st = _read_json(p / 'state.json') or {}
    st['status'] = 'done'
    _write_json(p / 'state.json', st)
    (p / 'notify').mkdir(exist_ok=True, parents=True)
    (p / 'notify' / 'done').write_text('1')
    return json.dumps(res, indent=2)


def _list_all_children_dirs(tree_id: str) -> List[Tuple[str, Path]]:
    base = Path('.egg/agents') / tree_id
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


def handle_tool_call(client: "ChatClient", call: Dict, display_call: bool = True):
    fn_name = call["function"]["name"]
    try:
        args_raw = call["function"].get("arguments", "{}")
        args = json.loads(args_raw or "{}") if isinstance(args_raw, str) else (args_raw or {})
    except json.JSONDecodeError:
        tool_msg = {"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": "Error: Invalid arguments."}
        client.messages.append(tool_msg)
        client.display_manager.render_message(tool_msg)
        return

    if display_call:
        client.console.print(json.dumps({"tool": fn_name, "args": args}, indent=2))

    execute = True if (client.in_single_turn_auto_execute_calls or client.yesToolFlag) else None
    if execute is None:
        while True:
            response = input(f"Execute the {fn_name} tool call? [y/n/a] ").strip().lower()
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

    if not execute:
        output = "--- SKIPPED BY USER ---"
    else:
        if fn_name == "bash":
            output = run_bash_script(args.get("script", ""))
        elif fn_name == "python":
            output = run_python_script(args.get("script", ""))
        elif fn_name == "pushContext":
            output = client.push_context(args.get("context", ""))
        elif fn_name == "popContext":
            output = client.pop_context(args.get("return_value", ""))
        elif fn_name == "str_replace_editor":
            output = str_replace_editor(args.get("file_path"), args.get("old_str"), args.get("new_str"))
        elif fn_name == "replace_lines":
            output = replace_lines(args.get("file_path"), args.get("start_line"), args.get("end_line"), args.get("new_content"))
        elif fn_name == "spawn_agent":
            output = tool_spawn_agent(args)
        elif fn_name == "wait_agents":
            try:
                output = tool_wait_agents(args)
            except KeyboardInterrupt:
                output = json.dumps({"interrupted": True, "message": "wait_agents interrupted by user"}, indent=2)
        elif fn_name == "write_result":
            output = tool_write_result(args)
        elif fn_name == "list_agents":
            output = tool_list_agents(args)
        elif fn_name == "spawn_agent_auto":
            output = tool_spawn_agent_auto(args)
        else:
            output = f"Unknown tool: {fn_name}"

    tool_msg = {"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": output}
    client.messages.append(tool_msg)
    client.display_manager.render_message(tool_msg)


def tool_list_agents(args: Dict) -> str:
    tree_id = args.get('tree_id') or os.environ.get('EG_TREE_ID')
    if not tree_id:
        try:
            tree_id = Path('.egg/agents/.current_tree').read_text().strip()
        except Exception:
            tree_id = None
    if not tree_id:
        return json.dumps({"error": "No tree context found"})
    listing: Dict[str, List[Dict[str, Any]]] = {}
    base = Path('.egg/agents') / tree_id
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

    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        current = Path('.egg/agents/.current_tree')
        if current.exists():
            try:
                tree_id = current.read_text().strip()
            except Exception:
                tree_id = None
    if not tree_id:
        tree_id = str(int(time.time()))
        Path('.egg/agents').mkdir(parents=True, exist_ok=True)
        Path('.egg/agents/.current_tree').write_text(tree_id)

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    parent_cwd = str(Path.cwd())

    base_dir = Path('.egg/agents') / tree_id / parent_id / 'children'
    base_dir.mkdir(parents=True, exist_ok=True)
    child_id = _next_child_id(base_dir, label)
    child_dir = base_dir / child_id
    child_dir.mkdir(parents=True, exist_ok=True)

    state = {
        "agent_id": child_id,
        "parent_id": parent_id,
        "status": "active",
        "model_key": "",
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
    _launch_child(session, parent_cwd, str(child_dir), child_id, tree_id, parent_id, extra_env={"EG_YES_TOOL_FLAG": "1"})

    return json.dumps({
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "dir": str(child_dir),
        "session": session
    }, indent=2)
