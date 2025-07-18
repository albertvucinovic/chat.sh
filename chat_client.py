import os
import sys
import json
import datetime
import re
import requests
import uuid
from pathlib import Path
from typing import List, Dict, Optional

import tiktoken
from rich.console import Console, Group
from rich.panel import Panel
from rich.live import Live
from rich.syntax import Syntax
from rich.text import Text
from prompt_toolkit.shortcuts import confirm

from executors import run_bash_script, run_python_script

TOOLS = [
    {"type": "function", "function": {"name": "bash", "description": "Execute a bash script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
    {"type": "function", "function": {"name": "python", "description": "Execute a Python script and return combined stdout/stderr.",
                                      "parameters": {"type": "object", "properties": {"script": {"type": "string"}}, "required": ["script"]}}},
]


class ChatClient:
    def __init__(self):
        self.headers = {"Content-Type": "application/json"}
        self.console = Console(force_terminal=True, legacy_windows=False)
        self.borders_enabled = True
        self.chat_dir = Path.cwd() / "localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.current_model_key = os.environ.get("API_MODEL", "OpenAI GPT-4o")
        self.base_url = None
        self.models_config = {}
        self.providers_config = {}
        self.summary: Optional[str] = None
        self.tools = TOOLS

        try:
            with open("models.json", "r") as f:
                self.models_config = json.load(f)
            with open("providers.json", "r") as f:
                self.providers_config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.console.print(
                f"[bold red]Error loading config: {e}[/bold red]")

        if self.current_model_key not in self.models_config:
            self.console.print(
                f"[bold yellow]Warning: Initial model '{self.current_model_key}' not in models.json.[/bold yellow]")
            if self.models_config:
                self.current_model_key = list(self.models_config.keys())[0]
            else:
                self.console.print(
                    "[bold red]Fatal: No models configured in models.json.[/bold red]")
                sys.exit(1)
        self.switch_model(self.current_model_key, initial_setup=True)

    def _build_system_prompt(self) -> str:
        system_prompt_content = "You are a helpful assistant."
        try:
            with open(Path(__file__).resolve().parent / "systemPrompt", "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            self.console.print(
                "[yellow]Warning: 'systemPrompt' file not found.[/yellow]")

        system_prompt_content += f"\nglobal folder: {Path(__file__).resolve().parent / 'global_commands'}\n"

        try:
            with open("AI.md", "r", encoding="utf-8") as f:
                aimd_content = f.read()
                if aimd_content:
                    system_prompt_content += "\nTHIS PROJECT'S INSTRUCTIONS AND RULES:\n\n" + aimd_content
        except FileNotFoundError:
            pass
        return system_prompt_content

    def _initialize_system_prompt(self):
        system_prompt_string = self._build_system_prompt()
        self.console.print(Panel(
            system_prompt_string, title="[bold cyan]System Prompt[/bold cyan]", border_style=self.get_border_style("dim")))
        self.messages: List[Dict] = [
            {"role": "system", "content": system_prompt_string}]

    def _update_provider_and_url(self):
        model_config = self.models_config.get(self.current_model_key)
        if not model_config:
            self.console.print(
                f"[bold red]Error: Current model key '{self.current_model_key}' not found.[/bold red]")
            return
        provider_name = model_config.get("provider")
        provider_config = self.providers_config.get(provider_name)
        if not provider_config:
            self.console.print(
                f"[bold red]Error: Provider '{provider_name}' not found.[/bold red]")
            return
        self.base_url = provider_config.get("api_base")
        api_key_env = provider_config.get("api_key_env")
        if api_key_env and (api_key := os.environ.get(api_key_env)):
            self.headers["Authorization"] = f"Bearer {api_key}"
        else:
            self.console.print(
                f"[bold red]Error: Env var '{api_key_env}' is not set for '{provider_name}'.[/bold red]")
            self.headers["Authorization"] = "Bearer NOT_SET"

    def switch_model(self, model_key: str, initial_setup: bool = False):
        if initial_setup:
            self._update_provider_and_url()
            self._initialize_system_prompt()
            return
        if not model_key:
            self.console.print("[bold]Available models:[/bold]")
            for name in self.models_config:
                self.console.print(f"- {name}")
            return
        if model_key not in self.models_config:
            self.console.print(
                f"[bold red]Unknown model: '{model_key}'[/bold red]")
            return
        self.current_model_key = model_key
        self._update_provider_and_url()
        self.console.print(
            f"[bold green]Switched to model: '{self.current_model_key}'[/bold green]")

    def send_message(self, message: str):
        self.messages.append({"role": "user", "content": message})
        while True:
            model_config = self.models_config.get(self.current_model_key, {})
            api_model_name = model_config.get("model_name")
            if not api_model_name:
                self.console.print(
                    "[bold red]API model name not found.[/bold red]")
                return
            assistant_text_parts, tool_calls_buf, interrupted = [], {}, False
            try:
                with Live(console=self.console, auto_refresh=False) as live:
                    live.update(
                        Panel("[dim]Assistant is thinking...[/dim]", border_style="cyan"), refresh=True)
                    response = requests.post(f"{self.base_url}", headers=self.headers, json={
                                             "model": api_model_name, "messages": self.messages, "tools": self.tools, "tool_choice": "auto", "stream": True}, timeout=120, stream=True)
                    response.raise_for_status()
                    for line_bytes in response.iter_lines():
                        if not line_bytes:
                            continue
                        line = line_bytes.decode('utf-8')
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            delta = json.loads(data).get("choices", [{}])[
                                0].get("delta", {})
                        except (json.JSONDecodeError, IndexError):
                            continue
                        if content := delta.get("content"):
                            assistant_text_parts.append(content)
                        if tool_calls_chunk := delta.get("tool_calls"):
                            for index, tc_delta in enumerate(tool_calls_chunk):
                                buffer_index = tc_delta.get("index", index)
                                if buffer_index not in tool_calls_buf:
                                    tool_calls_buf[buffer_index] = {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {
                                        "name": "", "arguments": ""}}
                                tc_full = tool_calls_buf[buffer_index]
                                if tc_delta.get("id"):
                                    tc_full["id"] = tc_delta["id"]
                                if f_delta := tc_delta.get("function"):
                                    if n := f_delta.get("name"):
                                        tc_full["function"]["name"] += n
                                    if a := f_delta.get("arguments"):
                                        tc_full["function"]["arguments"] += a
                        renderables = []
                        if assistant_text_parts:
                            renderables.append(
                                Text("".join(assistant_text_parts), justify="left"))
                        for tc_full in sorted(tool_calls_buf.values(), key=lambda x: x.get('index', 0)):
                            name, args = tc_full.get("function", {}).get(
                                "name", "..."), tc_full.get("function", {}).get("arguments", "")
                            try:
                                script = json.loads(
                                    args or '{}').get('script', args)
                                renderables.append(Panel(Syntax(script, name, theme="monokai", line_numbers=self.borders_enabled),
                                                   title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                            except (json.JSONDecodeError, AttributeError):
                                renderables.append(Panel(Text(
                                    args), title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                        live.update(Panel(Group(
                            *renderables), title="[bold cyan]Assistant[/bold cyan]", border_style="cyan"), refresh=True)
            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                self.console.print(f"\n[bold red]Error: {e}[/bold red]" if not isinstance(
                    e, KeyboardInterrupt) else "\n[bold yellow]Interrupted.[/bold yellow]")
                interrupted = True
            if interrupted:
                return
            assistant_msg = {"role": "assistant"}
            if text := "".join(assistant_text_parts):
                assistant_msg["content"] = text
            if tool_calls_buf:
                assistant_msg["tool_calls"] = list(tool_calls_buf.values())
            if not assistant_msg.get("content") and not assistant_msg.get("tool_calls"):
                return
            self.messages.append(assistant_msg)
            if tool_calls := assistant_msg.get("tool_calls"):
                for tc in tool_calls:
                    self._handle_tool_call(tc)
                continue
            break

    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        api_model_name = self.models_config.get(
            self.current_model_key, {}).get("model_name")
        if not api_model_name:
            self.console.print(
                "[bold red]API model name not found.[/bold red]")
            return
        try:
            requests.post(f"{self.base_url}", headers=self.headers, json={"model": api_model_name,
                          "messages": self.messages, "stream": False, "max_tokens": 1}, timeout=30).raise_for_status()
        except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
            self.console.print(
                f"\n[bold red]Error: Failed to send context to LLM: {e}[/bold red]")
            self.messages.pop()

    def _handle_tool_call(self, call: Dict):
        fn_name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments", "{}") or "{}")
            script = args.get("script", "")
        except json.JSONDecodeError:
            self.messages.append({"role": "tool", "name": fn_name,
                                 "tool_call_id": call["id"], "content": "Error: Invalid arguments."})
            return
        try:
            execute = confirm(f"Execute the {fn_name} tool call shown above?")
        except (EOFError, KeyboardInterrupt):
            execute = False
        if not execute:
            output = "--- SKIPPED BY USER ---"
            self.console.print("[yellow]Skipped by user.[/yellow]")
        else:
            self.console.print("[cyan]Executing...[/cyan]")
            output = run_bash_script(
                script) if fn_name == "bash" else run_python_script(script)
            self.console.print(Panel(Text(
                output), title="[bold green]Execution Output[/bold green]", border_style="green"))
        self.messages.append(
            {"role": "tool", "name": fn_name, "tool_call_id": call["id"], "content": output})

    def toggle_borders(self) -> str:
        self.borders_enabled = not self.borders_enabled
        return f"Borders are now {'ON' if self.borders_enabled else 'OFF'}"

    def get_border_style(self, style: str) -> str:
        return style if self.borders_enabled else "none"

    def save_chat(self) -> str:
        summary = "chat_summary"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_summary = re.sub(r"[^\w-]", "_", summary)
        file_path = self.chat_dir / f"{timestamp}_{safe_summary}.json"
        with open(file_path, "w") as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)

    def load_chat(self, chat_name: str):
        """
        Loads a previous chat from the localChats directory.
        Re-renders all tool calls with proper formatting.
        """
        self.console.print(f"Loading chat: {chat_name}")
        try:
            # Find the matching file
            matches = list(self.chat_dir.glob(chat_name + ".json"))
            if not matches:
                # Also check for partial matches that still uniquely identify a chat
                all_chats = [f.name for f in self.chat_dir.iterdir()
                             if f.suffix == ".json"]
                partial_matches = [f for f in all_chats if chat_name in f]
                if len(partial_matches) == 1:
                    chat_file = self.chat_dir / partial_matches[0]
                else:
                    self.console.print(
                        f"[bold red]Error: Chat file '{chat_name}.json' not found or not unique.[/bold red]")
                    self.console.print("Available chats:")
                    for f_name in sorted(all_chats, reverse=True):
                        self.console.print(f"- {f_name}")
                    return
            else:
                chat_file = matches[0]

            with open(chat_file, "r") as f:
                loaded_messages = json.load(f)

            # Clear the current messages and replace them
            self.messages.clear()
            # Render the loaded messages
            for msg in loaded_messages:
                self.messages.append(msg)  # Always append to history first

                if msg.get("role") == "system":
                    self.console.print(Panel(
                        msg["content"], title="[bold cyan]System Prompt[/bold cyan]", border_style=self.get_border_style("dim")))
                elif msg.get("role") == "user":
                    self.console.print(Panel(
                        msg["content"], title="[bold green]You[/bold green]", border_style="green"))
                elif msg.get("role") == "assistant":
                    # Handle assistant content and potential tool calls
                    renderables = []
                    if msg.get("content"):
                        renderables.append(
                            Text(msg["content"], justify="left"))
                    if msg.get("tool_calls"):
                        for tc_full in msg["tool_calls"]:
                            name, args = tc_full.get("function", {}).get(
                                "name", "..."), tc_full.get("function", {}).get("arguments", "")
                            try:
                                script = json.loads(
                                    args or '{}').get('script', args)
                                renderables.append(Panel(Syntax(script, name, theme="monokai", line_numbers=self.borders_enabled),
                                                   title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                            except (json.JSONDecodeError, AttributeError):
                                renderables.append(Panel(Text(
                                    args), title=f"[bold yellow]Tool Call: {name}[/bold yellow]", border_style="yellow"))
                    if renderables:
                        self.console.print(Panel(Group(
                            *renderables), title="[bold cyan]Assistant[/bold cyan]", border_style="cyan"))

                elif msg.get("role") == "tool":
                    # Handle tool output display
                    output_renderable = Text(msg["content"])
                    self.console.print(Panel(
                        output_renderable, title=f"[bold green]Tool Output: {msg.get('name', 'N/A')}[/bold green]", border_style="green"))

            self.console.print(
                "--- End of loaded conversation ---", style="dim")

        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.console.print(f"[bold red]Error loading chat: {e}[/bold red]")
