import os
import sys
import json
import datetime
import re
import requests
import uuid
import time
from pathlib import Path
from typing import List, Dict, Optional, Any

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
        # Keep borders visible; avoid Live repaint loops in tmux by centralizing streaming in DisplayManager
        self.console = Console(force_terminal=True, legacy_windows=False)
        self.display_manager = DisplayManager(self)
        self.headers = {"Content-Type": "application/json"}
        # Start with borders disabled by default
        self.borders_enabled = False
        self.chat_dir = Path.cwd() / ".egg/localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        self.current_model_key = None
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
            agent_dir = os.environ.get('EG_AGENT_DIR')
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

        # Determine initial model from env or config meta
        default_model_env = os.environ.get("DEFAULT_MODEL")
        default_from_config = None
        try:
            meta = self.providers_config.get("_meta", {}) if isinstance(self.providers_config, dict) else {}
            if isinstance(meta, dict):
                default_from_config = meta.get("default_model")
        except Exception:
            pass
        self.current_model_key = default_model_env or default_from_config
        if not self.current_model_key or self.current_model_key not in self.models_config:
            # Pick the first available model as a fallback
            self.current_model_key = list(self.models_config.keys())[0]
            if default_model_env or default_from_config:
                self.console.print(f"[bold yellow]Warning: Initial model '{default_model_env or default_from_config}' not found. Using '{self.current_model_key}'.[/bold yellow]")

        # Load all-models.json for dynamic provider-wide suggestions
        self._all_models_cache: Dict[str, Dict[str, Any]] = self._load_all_models()

        self.switch_model(self.current_model_key, initial_setup=True)

    def _all_models_path(self) -> Path:
        return Path(__file__).resolve().parent / "all-models.json"

    def _load_all_models(self) -> Dict[str, Dict[str, Any]]:
        p = self._all_models_path()
        if not p.exists():
            return {}
        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and 'providers' in data:
                return data['providers']
        except Exception:
            pass
        return {}

    def _save_all_models(self, providers_map: Dict[str, Dict[str, Any]]):
        out = {"providers": providers_map}
        p = self._all_models_path()
        try:
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(out, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.console.print(f"[red]Failed to write all-models.json: {e}[/red]")

    def get_providers(self) -> List[str]:
        return list(self.providers_config.keys()) if isinstance(self.providers_config, dict) else []

    def get_all_models_for_provider(self, provider: str) -> List[str]:
        prov = self._all_models_cache.get(provider) or {}
        models = prov.get('models')
        if isinstance(models, list):
            # allow either list of strings or list of dicts with id
            ids: List[str] = []
            for m in models:
                if isinstance(m, str):
                    ids.append(m)
                elif isinstance(m, dict) and m.get('id'):
                    ids.append(str(m['id']))
            return ids
        return []

    def get_all_models_suggestions(self, prefix: str) -> List[str]:
        # prefix starts with 'all:'
        base = 'all:'
        rest = prefix[len(base):]
        out: List[str] = []
        if ':' not in rest:
            # suggest providers
            for prov in sorted(self.get_providers()):
                cand = f"all:{prov}:"
                if cand.lower().startswith(prefix.lower()):
                    out.append(cand)
        else:
            prov, partial = rest.split(':', 1)
            for mid in self.get_all_models_for_provider(prov):
                cand = f"all:{prov}:{mid}"
                if cand.lower().startswith(prefix.lower()):
                    out.append(cand)
        return out

    def update_all_models(self, provider: str) -> str:
        if not provider:
            return "Error: provider not specified."
        prov_cfg = self.providers_config.get(provider) if isinstance(self.providers_config, dict) else None
        if not isinstance(prov_cfg, dict):
            return f"Error: Unknown provider '{provider}'."
        api_base = str(prov_cfg.get('api_base') or '')
        key_env = prov_cfg.get('api_key_env')
        api_key = os.environ.get(key_env) if key_env else None
        if not api_base:
            return f"Error: Provider '{provider}' is missing api_base in models.json."
        # Heuristically form models endpoint for OpenAI-compatible APIs
        models_url = api_base.rstrip('/')
        # replace known paths
        for seg in ("/chat/completions", "/completions", "/responses"):
            if models_url.endswith(seg):
                models_url = models_url[: -len(seg)]
                break
        if not models_url.endswith('/models'):
            models_url = models_url + '/models'
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            resp = requests.get(models_url, headers=headers, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            return f"Error: Failed to fetch models from {provider}: {e}"
        try:
            data = resp.json()
        except Exception as e:
            return f"Error: Non-JSON response from {provider}: {e}"
        # Parse results
        model_ids: List[str] = []
        if isinstance(data, dict):
            # OpenAI-compatible: { data: [ {id: ...}, ... ] }
            items = data.get('data')
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and it.get('id'):
                        model_ids.append(str(it['id']))
        # Fallback for direct lists
        if not model_ids and isinstance(data, list):
            for it in data:
                if isinstance(it, dict) and it.get('id'):
                    model_ids.append(str(it['id']))
                elif isinstance(it, str):
                    model_ids.append(it)
        if not model_ids:
            return f"Warning: No models parsed from {provider} at {models_url}."
        # Save into cache and persist
        self._all_models_cache[provider] = {
            'fetched_at': int(time.time()),
            'source': models_url,
            'models': model_ids,
        }
        self._save_all_models(self._all_models_cache)
        return f"Updated all-models.json for provider '{provider}' with {len(model_ids)} models."

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
        if not model_config:
            self.console.print(f"[bold red]Error: Model config for '{self.current_model_key}' not found.[/bold red]")
            return
        provider_name = model_config.get("provider")
        provider_config = self.providers_config.get(provider_name) if isinstance(self.providers_config, dict) else None
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
            # Pretty print available models grouped by provider
            by_provider: Dict[str, List[str]] = {}
            for name, cfg in self.models_config.items():
                by_provider.setdefault(cfg.get("provider", "unknown"), []).append(name)
            lines = []
            for prov in sorted(by_provider.keys()):
                lines.append(f"{prov}:")
                for m in sorted(by_provider[prov]):
                    lines.append(f"  - {m}")
            lines.append("\nTip: type 'all:' to see full provider catalogs (if downloaded via /updateAllModels). Use 'all:provider:model'.")
            self.console.print("[bold]Available models (by provider):[/bold]\n" + "\n".join(lines))
            return

        # Handle dynamic 'all:' models (never touching models.json)
        if model_key.lower().startswith('all:'):
            rest = model_key[4:]
            if ':' not in rest:
                self.console.print("[bold yellow]Usage:[/bold yellow] /model all:<provider>:<model_id>")
                return
            prov, _, mid = rest.partition(':')
            if not prov or not mid:
                self.console.print("[bold yellow]Usage:[/bold yellow] /model all:<provider>:<model_id>")
                return
            catalog = self.get_all_models_for_provider(prov)
            if mid not in catalog:
                self.console.print(f"[bold red]Unknown model '{mid}' for provider '{prov}'.[/bold red] Use /updateAllModels {prov} first, then try again.")
                return
            # Create an ephemeral entry in models_config
            virtual_key = f"all:{prov}:{mid}"
            self.models_config[virtual_key] = {
                "provider": prov,
                "model_name": mid,
                "alias": []
            }
            self.current_model_key = virtual_key
            self._update_provider_and_url()
            self.console.print(f"[bold green]Switched to provider catalog model: '{virtual_key}'[/bold green]")
            return

        # Resolve by exact name or alias
        resolved = None
        if model_key in self.models_config:
            resolved = model_key
        else:
            # Search aliases (case-insensitive)
            lk = model_key.lower()
            for display, cfg in self.models_config.items():
                aliases = [a.lower() for a in cfg.get("alias", [])]
                if lk in aliases:
                    resolved = display
                    break
        if not resolved:
            # Try provider-prefixed form: provider:name
            if ":" in model_key:
                prov, name = model_key.split(":", 1)
                for display, cfg in self.models_config.items():
                    if cfg.get("provider") == prov and (display == name or name.lower() in [a.lower() for a in cfg.get("alias", [])]):
                        resolved = display
                        break
        if not resolved:
            self.console.print(f"[bold red]Unknown model: '{model_key}'[/bold red]")
            # provide suggestions
            self.console.print("[bold]Tip:[/bold] Use /model to list grouped models, type 'all:' to select from full provider catalogs, or specify provider:name. Aliases are supported.")
            return
        self.current_model_key = resolved
        self._update_provider_and_url()
        self.console.print(f"[bold green]Switched to model: '{self.current_model_key}'[/bold green]")

    def _sanitize_messages_for_api(self, messages: List[Dict]) -> List[Dict]:
        """Prepare messages for provider: remove unsupported keys and exclude local-only tool outputs."""
        sanitized_messages = []
        keys_to_remove = {"reasoning_content", "model_key", "local_tool"}
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            # Exclude tool messages that were marked as local-only (from user-initiated commands)
            if role == "tool" and msg.get("local_tool"):
                continue
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
            in_tmux = bool(os.environ.get("TMUX"))
            # Begin streaming via DisplayManager
            self.display_manager.begin_stream(self.current_model_key, mode=("tmux" if in_tmux else "normal"))
            try:
                payload = {"model": api_model_name, "messages": messages_for_api, "tools": self.tools, "tool_choice": "auto", "stream": True}
                response = requests.post(f"{self.base_url}", headers=self.headers, json=payload, timeout=120, stream=True)
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
                            if idx not in tool_calls_buf:
                                tool_calls_buf[idx] = {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {"name": "", "arguments": ""}}
                            if tc_delta.get("id"): tool_calls_buf[idx]["id"] = tc_delta["id"]
                            if f_delta := tc_delta.get("function"):
                                if n := f_delta.get("name"): tool_calls_buf[idx]["function"]["name"] += n
                                if a := f_delta.get("arguments"): tool_calls_buf[idx]["function"]["arguments"] += a
                    
                    # Update display per delta
                    self.display_manager.stream_chunk(
                        content=delta.get("content"),
                        reasoning=delta.get("reasoning_content"),
                        tool_calls_delta=delta.get("tool_calls"),
                        model_name=self.current_model_key,
                        buffers={
                            "assistant_text_parts": assistant_text_parts,
                            "reasoning_parts": reasoning_parts,
                            "tool_calls_buf": tool_calls_buf,
                        }
                    )

            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                self.console.print(f"\n[bold red]Error: {e}[/bold red]" if not isinstance(e, KeyboardInterrupt) else "\n[bold yellow]Interrupted.[/bold yellow]")
                # Ensure we close any active streaming display
                try:
                    self.display_manager.end_stream({"role": "assistant", "content": "".join(assistant_text_parts), "tool_calls": list(tool_calls_buf.values())})
                except Exception:
                    pass
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

            # End streaming cleanly (normal mode closes Live; tmux prints a rule only)
            self.display_manager.end_stream(assistant_msg)
            
            if tool_calls := assistant_msg.get("tool_calls"):
                # In tmux mode we already streamed tool deltas; avoid extra display prints
                display_calls = should_redisplay and (self.display_manager._stream_mode != "tmux")
                for tc in tool_calls: tool_manager.handle_tool_call(self, tc, display_call=display_calls)
                continue
            break

    def send_context_only(self, context_message: str):
        """Send context to the model without changing turn or local transcript duplication.
        Does NOT append the context_message to self.messages. Builds a one-off payload.
        """
        api_model_name = self.models_config.get(self.current_model_key, {}).get("model_name")
        if not api_model_name:
            self.console.print("[bold red]API model name not found.[/bold red]")
            return
        # Build provider payload from sanitized history, then append a one-off user message
        base_messages = self._sanitize_messages_for_api(self.messages)
        one_off = base_messages + [{"role": "user", "content": context_message}]
        try:
            requests.post(
                f"{self.base_url}",
                headers=self.headers,
                json={
                    "model": api_model_name,
                    "messages": one_off,
                    "tools": self.tools,
                    "tool_choice": "auto",
                    "stream": False,
                    "max_tokens": 1
                },
                timeout=30
            ).raise_for_status()
        except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
            self.console.print(f"\n[bold red]Error: Failed to send context to LLM: {e}[/bold red]")

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
