import json
import re
import uuid
from typing import Dict, List

from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from prompt_toolkit.shortcuts import confirm

from executors import run_bash_script, run_python_script

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Execute a bash script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
    {"type": "function", "function": {"name": "python", "description": "Execute a Python script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
    {"type": "function", "function": {"name": "pushContext", "description": "Save current chat and start new context conversation.",
                                      "parameters": {"type": "object", "properties": {"context": {"type": "string"}}, "required": ["context"]}}},
    {"type": "function", "function": {"name": "popContext", "description": "Save current chat and restore previous context conversation.",
                                      "parameters": {"type": "object", "properties": {"return_value": {"type": "string"}}, "required": ["return_value"]}}},
]

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
    
    try:
        execute = client.yesTooolFlag or confirm(f"Execute the {fn_name} tool call shown above?")
    except (EOFError, KeyboardInterrupt):
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
        else: output = f"Unknown tool: {fn_name}"
        client.console.print(Panel(Text(output), title="[bold green]Execution Output[/bold green]", border_style="green", box=client.boxStyle))
        
    client.messages.append({"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": output})
