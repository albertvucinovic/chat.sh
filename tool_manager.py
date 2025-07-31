import json
import re
import time
import os
from pathlib import Path
from typing import Dict, List, Any

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

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
        "description": """
            Replace specific text in files. Requires exact matches including whitespace.
            Requirements:
            1) old_str MUST be the exact literal text to replace (including all whitespace, indentation, newlines, etc.)
            2) new_str MUST be the exact literal text to replace old_str (also including all whitespace, indentation, newlines, etc.).
            3) NEVER escape old_str of new_str, that would break the exact literal text requirement.
            ***Important:*** If ANY of the above are not satisfied, the tool will fail.
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to a file"},
                "old_str": {"type": "string", "description": "Exact string to replace"},
                "new_str": {"type": "string", "description": "Replacement string"},
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
                "file_path": {"type": "string", "description": "Path to the file."},
                "start_line": {"type": "integer", "description": "The starting line number (1-indexed)."},
                "end_line": {"type": "integer", "description": "The ending line number (1-indexed)."},
                "new_content": {"type": "string", "description": "The new content to insert, replacing the specified line range."},
            },
            "required": ["file_path", "start_line", "end_line", "new_content"]
        }
    }},
    {"type": "function", "function": {
        "name": "spawn_agent",
        "description": "Spawn a single child agent by inferring tree and parent from environment. Returns {tree_id,parent_id,child_id,dir,session}.",
        "parameters": {
            "type": "object",
            "properties": {
                "context_text": {"type": "string"},
                "label": {"type": "string"}
            },
            "required": ["context_text"]
        }
    }} ,
    {"type": "function", "function": {
        "name": "wait_agents",
        "description": "Wait for children to finish for current parent. which: 'all'|'any'|child_id. Optional timeout_sec.",
        "parameters": {
            "type": "object",
            "properties": {
                "which": {},
                "timeout_sec": {"type": "integer"}
            },
            "required": ["which"]
        }
    }} ,
    {"type": "function", "function": {
        "name": "write_result",
        "description": "Write result.json and mark done for the current agent directory. Args: agent_dir, return_value, summary?",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_dir": {"type": "string"},
                "return_value": {"type": "string"},
                "summary": {"type": "string"}
            },
            "required": ["agent_dir", "return_value"]
        }
    }}
]

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
    max_idx = 0
    if children_dir.exists():
        for d in children_dir.iterdir():
            if d.is_dir() and d.name.startswith(base + "-"):
                try:
                    idx = int(d.name.split('-')[-1])
                    max_idx = max(max_idx, idx)
                except ValueError:
                    continue
    return f"{base}-{max_idx+1:03d}"


def _launch_child(session: str, parent_cwd: str, agent_dir: str, child_id: str, tree_id: str, parent_id: str):
    # Use chat.sh entrypoint; keep working directory as parent's CWD and point EG_AGENT_DIR to agent_dir
    repo_root = Path(__file__).resolve().parent
    chat_sh = (repo_root / 'chat.sh').resolve()
    if not chat_sh.exists():
        # fallback to chat.py if chat.sh missing
        chat_py = (repo_root / 'chat.py').resolve()
        launch_cmd = f"python3 -u '{chat_py}'"
    else:
        launch_cmd = f"'{chat_sh}'"
    cmd = (
        f"cd '{parent_cwd}' && "
        f"EG_AGENT_DIR='{agent_dir}' EG_TREE_ID='{tree_id}' EG_PARENT_ID='{parent_id}' EG_AGENT_ID='{child_id}' "
        f"bash -lc {launch_cmd}"
    )
    run_bash_script(f"tmux new-window -t {session} -n {child_id} 'bash -lc \"{cmd}\"'")

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
    _write_json(p/ 'result.json', res)
    st = _read_json(p/ 'state.json') or {}
    st['status'] = 'done'
    _write_json(p/ 'state.json', st)
    (p/ 'notify').mkdir(exist_ok=True, parents=True)


def tool_spawn_agent(args: Dict) -> str:
    context_text = args.get('context_text', '').strip()
    label = (args.get('label') or 'child').strip() or 'child'
    # determine tree and parent
    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        # try to load last tree
        cur_file = Path('.egg/agents/.current_tree')
        if cur_file.exists():
            try: tree_id = cur_file.read_text().strip()
            except Exception: tree_id = None
    if not tree_id:
        tree_id = str(int(time.time()))
        Path('.egg/agents').mkdir(parents=True, exist_ok=True)
        Path('.egg/agents/.current_tree').write_text(tree_id)

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    # Parent working directory is the current process CWD
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
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": context_text}
    ]
    _write_json(child_dir / 'messages.json', messages)

    session = _ensure_session(tree_id)
    _launch_child(session, parent_cwd, str(child_dir), child_id, tree_id, parent_id)

    return json.dumps({
        "tree_id": tree_id,
        "parent_id": parent_id,
        "child_id": child_id,
        "dir": str(child_dir),
        "session": session
    }, indent=2)


def tool_wait_agents(args: Dict) -> str:
    which = args.get('which', 'all')
    timeout = int(args.get('timeout_sec', 0))
    tree_id = os.environ.get('EG_TREE_ID')
    if not tree_id:
        try: tree_id = Path('.egg/agents/.current_tree').read_text().strip()
        except Exception: tree_id = None
    if not tree_id:
        return json.dumps({"error": "No tree context found"})

    parent_id = os.environ.get('EG_AGENT_ID', 'root')
    agent_root = Path('.egg/agents') / tree_id / parent_id / 'children'

    start = time.time()
    results: Dict[str, Any] = {}

    def finished(child_dir: Path) -> bool:
        return (child_dir / 'result.json').exists()

    if isinstance(which, list):
        target_ids = which
    elif which in ('all', 'any'):
        target_ids = [d.name for d in agent_root.iterdir() if d.is_dir()]
    else:
        target_ids = [str(which)]

    pending = set(target_ids)
    while pending:
        for cid in list(pending):
            cdir = agent_root / cid
            if finished(cdir):
                try: results[cid] = _read_json(cdir/ 'result.json')
                except Exception: results[cid] = {"status": "done"}
                pending.remove(cid)
                if which == 'any':
                    pending.clear()
                    break
        if not pending:
            break
        if timeout and (time.time() - start) > timeout:
            break
        time.sleep(1)

    return json.dumps({"completed": list(results.keys()), "results": results, "pending": list(pending)}, indent=2)


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
    _write_json(p/ 'result.json', res)
    st = _read_json(p/ 'state.json') or {}
    st['status'] = 'done'
    _write_json(p/ 'state.json', st)
    (p/ 'notify').mkdir(exist_ok=True, parents=True)
    (p/ 'notify' / 'done').write_text('1')
    return json.dumps(res, indent=2)
