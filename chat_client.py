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
from rich.console import Console
from rich.panel import Panel
from rich.live import Live
from rich import box

from config import load_configs
from display import DisplayManager
import tool_manager


class ChatClient:
    def get_aimd_words_for_completion(self):
        """Extracts unique words (3+ chars) from AI.md content."""
        if not hasattr(self, "aimd_content") or not self.aimd_content:
            return []
        words = re.findall(r"\b\w{3,}\b", self.aimd_content)
        seen = set()
        # Return unique words, preserving order of first appearance
        return [w for w in words if (wl := w.lower()) not in seen and not seen.add(wl)]

    def get_recent_words_for_completion(self, limit=500):
        words = []
        messages = self.messages[-50:] if hasattr(self, "messages") else []
        for msg in messages:
            if msg.get("role") in ("user", "assistant") and msg.get("content"):
                words += re.findall(r"\b\w{3,}\b", msg["content"])
        seen = set()
        out = [w for w in words[::-1] if (wl := w.lower()) not in seen and not seen.add(wl)][:limit]
        return out[::-1]

    def __init__(self):
        self.console = Console(force_terminal=True, legacy_windows=False)
        self.display_manager = DisplayManager(self)
        self.headers = {"Content-Type": "application/json"}
        # Start with borders disabled by default
        self.borders_enabled = False
        self.chat_dir = Path.cwd() / ".egg/localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.current_model_key = os.environ.get("DEFAULT_MODEL", "OpenAI GPT-4o")
        self.base_url = None
        self.models_config, self.providers_config = load_configs()
        self.short_recap: Optional[str] = None
        self.tools = tool_manager.TOOLS
        self.context_stack = []
        self.original_system_prompt = ""
        self.aimd_content: str = ""
        # Use a minimal box when borders are disabled
        self.boxStyle = box.MINIMAL
        self.yesToolFlag = False
        # Enable auto tool-call approval for subagents spawned with EG_YES_TOOL_FLAG
        try:
            env_flag = os.environ.get("EG_YES_TOOL_FLAG", "").strip().lower()
            if env_flag in ("1", "true", "yes", "on"):
                self.yesToolFlag = True
        except Exception:
            pass
        # Also check agent state.json if running as a subagent
        try:
            agent_dir = os.environ.get("EG_AGENT_DIR")
            if agent_dir:
                st_path = Path(agent_dir) / 'state.json'
                if st_path.exists():
                    with open(st_path, 'r') as f:
                        st = json.load(f)
                    if isinstance(st, dict) and st.get('auto_tool_approve'):
                        self.yesToolFlag = True
        except Exception:
            pass
        self.show_thinking = True
        self.in_single_turn_auto_execute_calls = False

        if not self.models_config:
            self.console.print("[bold red]Fatal: No models configured in models.json.[/bold red]")
            sys.exit(1)
        if self.current_model_key not in self.models_config:
            self.console.print(f"[bold yellow]Warning: Initial model '{self.current_model_key}' not found. Using first available.[/bold yellow]")
            self.current_model_key = list(self.models_config.keys())[0]
        
        self.switch_model(self.current_model_key, initial_setup=True)

    def _build_system_prompt(self) -> str:
        system_prompt_content = "You are a helpful assistant."
        try:
            with open(Path(__file__).resolve().parent / "systemPrompt", "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            self.console.print("[yellow]Warning: 'systemPrompt' file not found.[/yellow]")
        system_prompt_content += f"\nglobal folder: {Path(__file__).resolve().parent / 'global_commands'}\n"
        try:
            with open("AI.md", "r", encoding="utf-8") as f:
                self.aimd_content = f.read()
                if self.aimd_content:
                    system_prompt_content += "\nTHIS PROJECT'S INSTRUCTIONS AND RULES:\n\n" + self.aimd_content
        except FileNotFoundError: pass
        return system_prompt_content

    def _initialize_system_prompt(self):
        system_prompt_string = self._build_system_prompt()
        self.display_manager.render_system_prompt(system_prompt_string)
        self.messages: List[Dict] = [{"role": "system", "content": system_prompt_string}]
        self.original_system_prompt = system_prompt_string

    def _clear_display(self):
        # Avoid clearing the screen in tmux; use a visual separator to keep scrollback intact
        try:
            self.console.rule("[dim]Context Switch[/dim]")
        except Exception:
            # Fallback to a simple separator if rule is unavailable
            self.console.print("\n" + ("-" * 40) + "\n")

    def push_context(self, file_path: Optional[str] = None, additional_text: str = "") -> str:
        """Save current chat and start completely fresh context, possibly from a file, with additional text."""
        final_context_parts = []
        context_identifier = ""
        
        if file_path:
            context_identifier = file_path.split('/')[-1]
            if file_path.startswith("global/"):
                script_dir = Path(__file__).resolve().parent
                file_full_path = script_dir / "global_commands" / file_path[len("global/"):]
            else:
                file_full_path = Path(file_path)
                
            try:
                with open(file_full_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                final_context_parts.append(file_content)
                
            except FileNotFoundError:
                error_msg = f"Error: File not found at '{file_full_path}'"
                self.console.print(f"[bold red]{error_msg}[/bold red]")
                return error_msg
            except Exception as e:
                error_msg = f"Error reading file: {e}"
                self.console.print(f"[bold red]{error_msg}[/bold red]")
                return error_msg

        if additional_text:
            if not context_identifier: # If no file, use part of additional_text as identifier
                context_identifier = additional_text[:30].replace('\n', ' ')
            final_context_parts.append(additional_text)

        if not final_context_parts:
            return "Error: No context or file provided for pushContext."

        combined_context = "\n\n".join(final_context_parts).strip()
        
        # Add the instruction to popContext
        instruction = "[SYSTEM NOTE: Your current task is to follow the instructions in this context. When you are finished, you MUST call the `popContext` tool with a summary of the outcome as the `return_value`. If your outcome is a markdown file, please call popContext with a path to the file you created.]\n\n"
        final_user_message_content = instruction + combined_context

        saved_file_path = self._save_chat_messages_to_file(self.messages, "pushed_context", context_identifier or "no_file")
        self.context_stack.append(saved_file_path)
        self._clear_display()
        
        # Start new context with the system prompt and the combined user message
        self.messages = [{"role": "system", "content": self.original_system_prompt}, {"role": "user", "content": final_user_message_content}]
        
        self.display_manager.render_system_prompt(self.original_system_prompt)
        self.display_manager.render_message({"role": "user", "content": final_user_message_content})
        
        self.console.print(Panel(f"[bold green]⬇️ Context Pushed[/bold green]", title="[bold]Entering New Context[/bold]", border_style="green", box=self.boxStyle))
        self.console.print(f"[dim]Previous context saved to: {Path(saved_file_path).name}[/dim]")
        
        return f"Entered new context from: {file_path if file_path else 'additional text'}"

    def pop_context(self, return_value: str) -> str:
        # If running within an agent directory, write result.json for FS-based tree
        agent_dir = os.environ.get('EG_AGENT_DIR')
        if agent_dir:
            try:
                result_path = Path(agent_dir) / 'result.json'
                res = {
                    "status": "done",
                    "return_value": return_value,
                    "short_recap": self.short_recap or "",
                    "finished_at": int(datetime.datetime.now().timestamp())
                }
                with open(result_path, 'w') as f:
                    json.dump(res, f, indent=2)
                # update state
                st_path = Path(agent_dir) / 'state.json'
                try:
                    with open(st_path, 'r') as f:
                        st = json.load(f)
                except Exception:
                    st = {}
                st['status'] = 'done'
                with open(st_path, 'w') as f:
                    json.dump(st, f, indent=2)
                # notify
                notify_dir = Path(agent_dir) / 'notify'
                notify_dir.mkdir(exist_ok=True, parents=True)
                (notify_dir / 'done').write_text('1')
            except Exception as e:
                self.console.print(f"[bold red]Error writing agent result: {e}[/bold red]")
        
        current_sub_context_file = self._save_chat_messages_to_file(self.messages, "popped_context", return_value)
        if not self.context_stack:
            self._clear_display()
            self.messages = [{"role": "system", "content": self.original_system_prompt}, {"role": "user", "content": f"Return value from push/pop context: {return_value}"}]
            self.display_manager.render_system_prompt(self.original_system_prompt)
            self.display_manager.render_message(self.messages[1])
            self.console.print(Panel(f"[bold red]⬆️ Context Pop (Stack Empty)[/bold red]", title="[bold]Context Management[/bold]", border_style="red", box=self.boxStyle))
            self.console.print(f"[dim]Current conversation saved: {Path(current_sub_context_file).name}[/dim]")
            # In subagent mode, exit process after finishing
            try:
                if agent_dir:
                    self.console.print("[bold green]Subagent finished. Exiting...[/bold green]")
                    sys.exit(0)
            except Exception:
                pass
            return f"No previous context to return to. Return value: {return_value}"
        
        previous_context_file = self.context_stack.pop()
        self._clear_display()
        try:
            with open(previous_context_file, "r") as f: self.messages = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.console.print(f"[bold red]Error loading previous context: {e}[/bold red]")
            self.messages = [{"role": "system", "content": self.original_system_prompt}]

        self.messages.append({"role": "user", "content": f"Return value from push/pop context: {return_value}"})
        for i, msg in enumerate(self.messages):
            if msg.get("role") == "system" and i == 0: self.display_manager.render_system_prompt(msg["content"])
            else: self.display_manager.render_message(msg)
        
        self.console.print(Panel(f"[bold yellow]⬆️ Context Popped[/bold yellow]", title="[bold]Returning to Previous Context[/bold]", border_style="yellow", box=self.boxStyle))
        self.console.print(f"[dim]Current sub-context saved: {Path(current_sub_context_file).name}[/dim]")
        self.console.print(f"[dim]Restored from: {Path(previous_context_file).name}[/dim]")
        return f"Restored previous context. Return value: {return_value}"

    def _update_provider_and_url(self):
        model_config = self.models_config.get(self.current_model_key)
        provider_name = model_config.get("provider")
        provider_config = self.providers_config.get(provider_name)
        if not provider_config:
            self.console.print(f"[bold red]Error: Provider '{provider_name}' not found.[/bold red]")
            return
        self.base_url = provider_config.get("api_base")
        api_key_env = provider_config.get("api_key_env")
        if api_key_env and (api_key := os.environ.get(api_key_env)):
            self.headers["Authorization"] = f"Bearer {api_key}"
        else:
            self.console.print(f"[bold red]Error: Env var '{api_key_env}' is not set for '{provider_name}'.[/bold red]")
            self.headers["Authorization"] = "Bearer NOT_SET"

    def switch_model(self, model_key: str, initial_setup: bool = False):
        if initial_setup:
            self._update_provider_and_url()
            self._initialize_system_prompt()
            return
        if not model_key:
            self.console.print("[bold]Available models:[/bold]\n" + "\n".join(f"- {name}" for name in self.models_config))
            return
        if model_key not in self.models_config:
            self.console.print(f"[bold red]Unknown model: '{model_key}'[/bold red]")
            return
        self.current_model_key = model_key
        self._update_provider_and_url()
        self.console.print(f"[bold green]Switched to model: '{self.current_model_key}'[/bold green]")

    def _sanitize_messages_for_api(self, messages: List[Dict]) -> List[Dict]:
        """Removes any non-standard keys from messages before sending to the API."""
        sanitized_messages = []
        keys_to_remove = {"reasoning_content", "model_key"} 
        for msg in messages:
            if isinstance(msg, dict):
                sanitized_msg = {key: value for key, value in msg.items() if key not in keys_to_remove}
                if sanitized_msg.get("content") is None and "tool_calls" not in sanitized_msg:
                     sanitized_msg["content"] = ""
                if sanitized_msg.get("role") == "assistant" and "tool_calls" in sanitized_msg and not sanitized_msg["tool_calls"]:
                    del sanitized_msg["tool_calls"]
                sanitized_messages.append(sanitized_msg)
        return sanitized_messages

    def send_message(self, message: str):
        # Add the model key to the user message for persistent storage
        self.messages.append({"role": "user", "content": message, "model_key": self.current_model_key})
        while True:
            model_config = self.models_config.get(self.current_model_key, {})
            api_model_name = model_config.get("model_name")
            if not api_model_name:
                self.console.print("[bold red]API model name not found.[/bold red]"); return
            
            messages_for_api = self._sanitize_messages_for_api(self.messages)
            
            assistant_text_parts, reasoning_parts, tool_calls_buf, interrupted = [], [], {}, False
            try:
                with Live(console=self.console, auto_refresh=False, vertical_overflow="visible") as live:
                    live.update(self.display_manager.create_live_display(None, {}), refresh=True)
                    response = requests.post(f"{self.base_url}", headers=self.headers, json={"model": api_model_name, "messages": messages_for_api, "tools": self.tools, "tool_choice": "auto", "stream": True}, timeout=120, stream=True)
                    response.raise_for_status()
                    
                    for line in response.iter_lines():
                        if not line: continue
                        line_str = line.decode('utf-8')
                        if not line_str.startswith("data: "): continue
                        data_str = line_str[6:]
                        if data_str == "[DONE]": break
                        try: delta = json.loads(data_str).get("choices", [{}])[0].get("delta", {})
                        except (json.JSONDecodeError, IndexError): continue
                        
                        if content := delta.get("content"): assistant_text_parts.append(content)
                        if reason := delta.get("reasoning_content"): reasoning_parts.append(reason)
                        if tc_chunk := delta.get("tool_calls"):
                            for tc_delta in tc_chunk:
                                idx = tc_delta.get("index")
                                if idx not in tool_calls_buf: tool_calls_buf[idx] = {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {"name": "", "arguments": ""}}
                                if tc_delta.get("id"): tool_calls_buf[idx]["id"] = tc_delta["id"]
                                if f_delta := tc_delta.get("function"):
                                    if n := f_delta.get("name"): tool_calls_buf[idx]["function"]["name"] += n
                                    if a := f_delta.get("arguments"): tool_calls_buf[idx]["function"]["arguments"] += a
                        
                        live.update(self.display_manager.create_live_display(
                            "".join(reasoning_parts),
                            {"content": "".join(assistant_text_parts), "tool_calls": list(tool_calls_buf.values())}
                        ), refresh=True)

            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                self.console.print(f"\n[bold red]Error: {e}[/bold red]" if not isinstance(e, KeyboardInterrupt) else "\n[bold yellow]Interrupted.[/bold yellow]")
                if isinstance(e, KeyboardInterrupt): return
            
            complete_message = "".join(assistant_text_parts)
            should_redisplay = False
            if not tool_calls_buf and complete_message.strip():
                parsed_tool_calls = tool_manager.parse_tool_calls_from_content(complete_message)
                if parsed_tool_calls:
                    tool_calls_buf = {i: {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": tc.get("function", tc)} for i, tc in enumerate(parsed_tool_calls)}
                    stripped = complete_message.strip()
                    if stripped.startswith(("{", "[")) and stripped.endswith(("}", "]")): should_redisplay = True
            
            assistant_msg = {"role": "assistant"}
            if complete_message: assistant_msg["content"] = complete_message
            if tool_calls_buf: assistant_msg["tool_calls"] = list(tool_calls_buf.values())
            
            # Add the model key to the assistant message for persistent storage
            assistant_msg["model_key"] = self.current_model_key
            
            if assistant_msg.get("content"): self.short_recap = self.extract_short_recap(assistant_msg.get("content"))
            self.messages.append(assistant_msg)
            
            if tool_calls := assistant_msg.get("tool_calls"):
                for tc in tool_calls: tool_manager.handle_tool_call(self, tc, display_call=should_redisplay)
                continue
            break

    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        api_model_name = self.models_config.get(self.current_model_key, {}).get("model_name")
        if not api_model_name: self.console.print("[bold red]API model name not found.[/bold red]"); return
        
        messages_for_api = self._sanitize_messages_for_api(self.messages)
        
        try:
            requests.post(f"{self.base_url}", headers=self.headers, json={"model": api_model_name, "messages": messages_for_api, "stream": False, "max_tokens": 1}, timeout=30).raise_for_status()
        except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
            self.console.print(f"\n[bold red]Error: Failed to send context to LLM: {e}[/bold red]")
            self.messages.pop()

    def toggle_borders(self) -> str:
        self.borders_enabled = not self.borders_enabled
        self.boxStyle = box.ROUNDED if self.borders_enabled else box.MINIMAL
        status = 'ON' if self.borders_enabled else 'OFF'
        self.console.print(f"Borders are now {status}")
        return f"Borders are now {status}"

    def toggle_thinking_display(self):
        self.show_thinking = not self.show_thinking
        status = "ON" if self.show_thinking else "OFF"
        self.console.print(f"Thinking display is now {status}")

    def get_border_style(self, style: str) -> str:
        return style if self.borders_enabled else "none"

    def extract_short_recap(self, text):
        try: return text.split("<short_recap>")[1].split("</short_recap>")[0].strip()
        except IndexError: return None

    def save_chat(self) -> str:
        short_recap = self.short_recap if self.short_recap else "chat_summary"
        return self._save_chat_messages_to_file(self.messages, "", short_recap)

    def _save_chat_messages_to_file(self, messages_to_save: List[Dict], file_prefix: str, identifier: str = "") -> str:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_identifier = re.sub(r"[^\w-]", "_", identifier[:30]) if identifier else ""
        file_name = f"{timestamp}_{file_prefix}_{safe_identifier}.json" if safe_identifier else f"{timestamp}_{file_prefix}.json"
        file_path = self.chat_dir / file_name
        with open(file_path, "w") as f: json.dump(messages_to_save, f, indent=2)
        return str(file_path)

    def load_chat(self, chat_name: str):
        self.console.print(f"Loading chat: {chat_name}")
        chat_file = None
        
        potential_file = self.chat_dir / chat_name
        if potential_file.is_file():
            chat_file = potential_file
        else:
            all_chats = [f for f in self.chat_dir.iterdir() if f.suffix == ".json"]
            partial_matches = [f for f in all_chats if chat_name in f.name]
            if len(partial_matches) == 1:
                chat_file = partial_matches[0]
            elif len(partial_matches) > 1:
                self.console.print(f"[bold red]Error: Chat name '{chat_name}' is ambiguous. Matches:[/bold red]")
                for f in partial_matches: self.console.print(f"- {f.name}")
                return
        
        if not chat_file:
            self.console.print(f"[bold red]Error: Chat file for '{chat_name}' not found.[/bold red]")
            return

        try:
            with open(chat_file, "r") as f: loaded_messages = json.load(f)
            self._clear_display()
            self.messages.clear()
            for i, msg in enumerate(loaded_messages):
                self.messages.append(msg)
                if msg.get("role") == "system" and i == 0: self.display_manager.render_system_prompt(msg["content"])
                else: self.display_manager.render_message(msg)
            self.console.print("--- End of loaded conversation ---", style="dim")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.console.print(f"[bold red]Error loading chat: {e}[/bold red]")
