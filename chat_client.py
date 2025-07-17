import os
import sys
import json
import datetime
import re
import requests
import uuid
from pathlib import Path
from typing import List, Dict, Optional

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.panel import Panel
from rich.live import Live
from rich.text import Text
from prompt_toolkit.shortcuts import confirm

from executors import run_bash_script, run_python_script

# TOOLS definition is unchanged
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash script and return combined stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {"script": {"type": "string"}},
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute a Python script and return combined stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {"script": {"type": "string"}},
                "required": ["script"],
            },
        },
    },
]

class ChatClient:
    def __init__(self):
        self.base_url = os.getenv("API_BASE")
        self.token = os.environ.get("API_KEY")
        if not self.token:
            raise ValueError("API token must be provided via API_KEY environment variable")

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        self.console = Console()

        self.chat_dir = Path.cwd() / "localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)

        try:
            script_dir = Path(__file__).resolve().parent
            system_prompt_path = script_dir / "systemPrompt"
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            system_prompt_content = "You are a helpful assistant."
            self.console.print("[yellow]Warning: 'systemPrompt' file not found. Using default.[/yellow]")

        system_prompt_content += f'\n\nYou are using this model: {os.environ.get("API_MODEL")}'
        system_prompt_content += f'\nglobal folder: {Path(__file__).resolve().parent / "global_commands"}'
        
        self.console.print(Panel(
            system_prompt_content,
            title="[bold cyan]System Prompt[/bold cyan]",
            border_style="dim"
        ))

        self.messages: List[Dict] = [{"role": "system", "content": system_prompt_content}]
        self.summary: Optional[str] = None
        self.tools = TOOLS

    def extract_summary(self, text):
        start_tag, end_tag = "<summary>", "</summary>"
        start_index = text.rfind(start_tag)
        if start_index == -1: return None
        start_index += len(start_tag)
        end_index = text.find(end_tag, start_index)
        if end_index == -1: return None
        return text[start_index:end_index].strip()

    def _handle_tool_call(self, call: Dict):
        fn_name = call["function"]["name"]
        try:
            args_str = call["function"].get("arguments", "{}") or "{}"
            args = json.loads(args_str)
            script = args.get("script", "")
        except json.JSONDecodeError:
            self.console.print(f"\n[bold red]Error: Could not decode arguments for tool {fn_name}.[/bold red]")
            return

        call_id = call["id"]
        
        syntax = Syntax(script, fn_name, theme="monokai", line_numbers=True)
        panel = Panel(syntax, title=f"[bold yellow]Tool Call: {fn_name}[/bold yellow]", border_style="yellow")
        self.console.print(panel)

        try:
            execute = confirm(f"Execute the above '{fn_name}' tool call?")
        except (EOFError, KeyboardInterrupt):
            execute = False

        if not execute:
            output = "--- SKIPPED BY USER ---"
            self.console.print("[yellow]Skipped by user.[/yellow]")
        else:
            self.console.print("[cyan]Executing...[/cyan]")
            output = run_bash_script(script) if fn_name == "bash" else run_python_script(script)
            self.console.print(Panel(Text(output), title="[bold green]Execution Output[/bold green]", border_style="green"))

        self.messages.append({
            "role": "tool", "name": fn_name, "tool_call_id": call_id, "content": output
        })

    def send_message(self, message: str):
        self.messages.append({"role": "user", "content": message})

        while True:
            assistant_text_parts: list[str] = []
            tool_calls_buf: dict[int, dict] = {}
            interrupted = False

            try:
                response = requests.post(
                    f"{self.base_url}",
                    headers=self.headers,
                    json={
                        "model": os.environ.get("API_MODEL"),
                        "messages": self.messages,
                        "tools": self.tools,
                        "tool_choice": "auto",
                        "stream": True,
                    },
                    timeout=120,
                    stream=True,
                )
                response.raise_for_status()

                # --- THE FIX: Manual byte decoding ---
                # We will manage a buffer to handle lines split across chunks.
                buffer = ""
                with Live(console=self.console, auto_refresh=False) as live:
                    live.update(Panel("[dim]Assistant is thinking...[/dim]", border_style="cyan"), refresh=True)
                    
                    # Iterate over raw byte chunks instead of decoded lines
                    for chunk in response.iter_content(chunk_size=1024):
                        if not chunk:
                            continue
                        
                        # Decode the byte chunk to UTF-8 and add to our buffer
                        buffer += chunk.decode('utf-8')
                        
                        # Process all complete lines in the buffer
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)

                            if not line.strip() or not line.startswith("data: "):
                                continue
                            
                            data = line[6:]
                            if data == "[DONE]":
                                break

                            try:
                                chunk_json = json.loads(data)
                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                            except json.JSONDecodeError:
                                continue # Skip malformed data lines

                            if delta.get("content"):
                                assistant_text_parts.append(delta["content"])

                            if tool_calls_chunk := delta.get("tool_calls"):
                                for index, tc_delta in enumerate(tool_calls_chunk):
                                    buffer_index = tc_delta.get("index", index)
                                    if buffer_index not in tool_calls_buf:
                                        tool_calls_buf[buffer_index] = {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {"name": "", "arguments": ""}}
                                    tc_full = tool_calls_buf[buffer_index]
                                    if tc_delta.get("id"): tc_full["id"] = tc_delta["id"]
                                    if f_delta := tc_delta.get("function"):
                                        if f_delta.get("name"): tc_full["function"]["name"] = f_delta["name"]
                                        if f_delta.get("arguments"): tc_full["function"]["arguments"] += f_delta["arguments"]
                            
                            # Update the live display inside the loop
                            renderables = []
                            if assistant_text_parts:
                                renderables.append(Markdown("".join(assistant_text_parts)))
                            for tc_full in sorted(tool_calls_buf.values(), key=lambda x: x.get('index', 0)):
                                name = tc_full.get("function", {}).get("name", "...")
                                args = tc_full.get("function", {}).get("arguments", "")
                                try:
                                    script = json.loads(args or '{}').get('script', args)
                                    syntax = Syntax(script, name, theme="monokai", line_numbers=True)
                                    renderables.append(Panel(syntax, title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                                except json.JSONDecodeError:
                                    renderables.append(Panel(Text(args), title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                            
                            live.update(Panel(Group(*renderables), title="[bold cyan]Assistant[/bold cyan]", border_style="cyan"), refresh=True)
                        
                        if data == "[DONE]":
                            break

            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                if isinstance(e, KeyboardInterrupt):
                    self.console.print("\n[bold yellow]Generation interrupted by user.[/bold yellow]")
                else:
                    self.console.print(f"\n[bold red]Error: {e}[/bold red]")
                interrupted = True
            
            # --- Post-stream processing is the same as before ---
            full_text = "".join(assistant_text_parts).strip()
            
            if not tool_calls_buf and full_text.strip().startswith('{'):
                try:
                    potential_tool_json = json.loads(full_text)
                    if isinstance(potential_tool_json, dict) and "tool_calls" in potential_tool_json:
                        parsed_tool_calls = potential_tool_json["tool_calls"]
                        for i, malformed_tc in enumerate(parsed_tool_calls):
                            standard_tc = {
                                "id": malformed_tc.get("id", f"call_{uuid.uuid4().hex[:10]}"),
                                "type": "function",
                                "function": {
                                    "name": malformed_tc.get("name", "unknown_tool"),
                                    "arguments": json.dumps(malformed_tc.get("arguments", {}))
                                }
                            }
                            tool_calls_buf[i] = standard_tc
                        full_text = ""
                except json.JSONDecodeError:
                    pass

            assistant_msg: dict = {"role": "assistant"}
            if full_text:
                assistant_msg["content"] = full_text
                self.summary = self.extract_summary(full_text)
            if tool_calls_buf:
                assistant_msg["tool_calls"] = list(tool_calls_buf.values())
            
            if assistant_msg.get("content") or assistant_msg.get("tool_calls"):
                self.messages.append(assistant_msg)

            if interrupted: return
            if not tool_calls_buf: return

            for tc in assistant_msg.get("tool_calls", []):
                self._handle_tool_call(tc)
            
            continue

    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        try:
            requests.post(
                f"{self.base_url}",
                headers=self.headers,
                json={"model": os.environ.get("API_MODEL"), "messages": self.messages, "stream": False, "max_tokens": 1},
                timeout=120,
            ).raise_for_status()
        except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
            self.console.print(f"\n[bold red]Error: Failed to send context to LLM: {e}[/bold red]")
            self.messages.pop()


    def save_chat(self) -> str:
        summary = self.summary if self.summary else "unnamed_chat"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_summary = re.sub(r"[^\w-]", "_", summary)
        chat_name = f"{timestamp}_{safe_summary}.json"
        file_path = self.chat_dir / chat_name
        with open(file_path, "w") as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)

    def load_chat(self, chat_name: str):
        chat_file = self.chat_dir / chat_name
        if not chat_file.is_file():
            chat_file = self.chat_dir / f"{chat_name}.json"

        if chat_file.exists():
            with open(chat_file, "r") as f:
                self.messages = json.load(f)
            self.console.print(Panel(f"Loaded chat: {chat_name}", style="green"))
            self.console.print(Panel("--- Previous conversation ---", style="dim"))
            for msg in self.messages:
                if msg["role"] == "system": continue
                
                title, style, content_renderable = "", "", None
                if msg["role"] == "user":
                    title, style = "[bold green]You[/bold green]", "green"
                    content_renderable = Markdown(msg.get('content', ''))
                elif msg["role"] == "assistant":
                    title, style = "[bold cyan]Assistant[/bold cyan]", "cyan"
                    content = msg.get("content", "")
                    tool_calls = msg.get("tool_calls")
                    renderables = []
                    if content: renderables.append(Markdown(content))
                    if tool_calls:
                        for tc in tool_calls:
                            fn_name = tc.get("function", {}).get("name", "unknown")
                            args_str = tc.get("function", {}).get("arguments", "{}")
                            try:
                                script = json.loads(args_str).get('script', '')
                                syntax = Syntax(script, fn_name, theme="monokai", line_numbers=True)
                                renderables.append(Panel(syntax, title=f"[bold yellow]Tool Call: {fn_name}[/bold yellow]", border_style="yellow"))
                            except:
                                renderables.append(Text(f"Tool Call: {fn_name}\n{args_str}"))
                    content_renderable = Group(*renderables)

                if content_renderable:
                    self.console.print(Panel(content_renderable, title=title, border_style=style))
            self.console.print(Panel("--- End of previous conversation ---", style="dim"))
        else:
            self.console.print(f"[bold red]Chat file not found:[/bold red] {chat_name}")
