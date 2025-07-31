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


def _launch_child(session: str, child_dir: str, child_id: str, tree_id: str, parent_id: str):
    # find repo root chat.py path via git ls-files, per project rule use git grep/ls-files
    cmd = (
        "cd '" + child_dir + "' && "
        f"EG_AGENT_DIR='{child_dir}' EG_TREE_ID='{tree_id}' EG_PARENT_ID='{parent_id}' EG_AGENT_ID='{child_id}' "
        "python3 -u $(git ls-files|grep chat.py)"
    )
    run_bash_script(f"tmux new-window -t {session} -n {child_id} 'bash -lc \"{cmd}\"'")


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
        "cwd": str(child_dir)
    }
    _write_json(child_dir / 'state.json', state)
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": context_text}
    ]
    _write_json(child_dir / 'messages.json', messages)

    session = _ensure_session(tree_id)
    _launch_child(session, str(child_dir), child_id, tree_id, parent_id)

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


def parse_tool_calls_from_content(message_content: str) -> list:
    """
    Attempt to parse a complete message as JSON tool calls.
    Handles various formats including direct JSON, JSON in markdown, and multiple
    tool call structures (standard API and simplified).
    Returns a list of tool calls if successful, empty list otherwise.
    """
    tool_calls = []
    
    if not message_content or not message_content.strip():
        return tool_calls

    def process_tc_list(tc_list: List[Dict]) -> List[Dict]:
        processed = []
        for tc in tc_list:
            if not isinstance(tc, dict): continue
            name, args = None, {}
            if 'function' in tc and isinstance(tc.get('function'), dict):
                func_dict = tc['function']
                name, args = func_dict.get('name'), func_dict.get('arguments', {})
            elif 'name' in tc:
                name, args = tc.get('name'), tc.get('arguments', {})
            
            if name:
                args_str = json.dumps(args) if not isinstance(args, str) else (args or '{}')
                try: json.loads(args_str)
                except json.JSONDecodeError: continue
                processed.append({"type": "function", "function": {"name": name, "arguments": args_str}})
        return processed

    try:
        parsed = json.loads(message_content.strip())
        if isinstance(parsed, dict) and 'tool_calls' in parsed and isinstance(parsed.get('tool_calls'), list):
            return process_tc_list(parsed['tool_calls'])
        elif isinstance(parsed, list):
            return process_tc_list(parsed)
        elif isinstance(parsed, dict):
            return process_tc_list([parsed])
    except json.JSONDecodeError:
        pass
    
    json_pattern = r'```(?:json)?\s*(.*?)\s*```'
    for match in re.findall(json_pattern, message_content, re.DOTALL):
        parsed_calls = parse_tool_calls_from_content(match)
        if parsed_calls: tool_calls.extend(parsed_calls)
    if tool_calls: return tool_calls
    
    function_pattern = r'"type"\s*:\s*"function"[^}]*"name"\s*:\s*"([^"]+)"[^}]*"arguments"\s*:\s*({[^{}]*(?:{[^{}]*}[^{}]*)*})'
    for name, args_str in re.findall(function_pattern, message_content):
        try:
            json.loads(args_str)
            tool_calls.append({"type": "function", "function": {"name": name, "arguments": args_str}})
        except json.JSONDecodeError: continue
    
    return tool_calls


def handle_tool_call(client: "ChatClient", call: Dict, display_call: bool = True):
    fn_name = call["function"]["name"]
    try:
        args_raw = call["function"].get("arguments", "{}")
        args = json.loads(args_raw or "{}") if isinstance(args_raw, str) else (args_raw or {})
    except json.JSONDecodeError:
        client.messages.append({"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": "Error: Invalid arguments."})
        return
    
    if display_call:
        display_content, syntax_lang = "", "json"
        if isinstance(args, dict) and "script" in args:
            display_content, syntax_lang = args.get("script", ""), fn_name
        else:
            display_content = json.dumps(args, indent=2) if args else "{}"
        client.console.print(Panel(
            Syntax(display_content, syntax_lang, theme="monokai", line_numbers=client.borders_enabled), 
            title=f"[bold yellow]Tool Call: {fn_name}[/bold yellow]", border_style="yellow", box=client.boxStyle))
    
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
        client.console.print("[yellow]Skipped by user.[/yellow]")
    else:
        client.console.print("[cyan]Executing...[/cyan]")
        if fn_name == "bash": output = run_bash_script(args.get("script", ""))
        elif fn_name == "python": output = run_python_script(args.get("script", ""))
        elif fn_name == "pushContext": output = client.push_context(args.get("context", ""))
        elif fn_name == "popContext": output = client.pop_context(args.get("return_value", ""))
        elif fn_name == "str_replace_editor":
            output = str_replace_editor(
                args.get("file_path"),
                args.get("old_str"),
                args.get("new_str")
            )
        elif fn_name == "replace_lines":
            output = replace_lines(
                args.get("file_path"),
                args.get("start_line"),
                args.get("end_line"),
                args.get("new_content")
            )
        elif fn_name == "spawn_agent":
            output = tool_spawn_agent(args)
        elif fn_name == "wait_agents":
            output = tool_wait_agents(args)
        elif fn_name == "write_result":
            output = tool_write_result(args)
        else: output = f"Unknown tool: {fn_name}"
        client.console.print(Panel(Text(output), title="[bold green]Execution Output[/bold green]", border_style="green", box=client.boxStyle))
        
    client.messages.append({"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": output})
