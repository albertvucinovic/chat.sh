import os
import sys
import json
import datetime
import re
import requests
from pathlib import Path
from typing import List, Dict, Optional
from executors import run_bash_script, run_python_script
import threading
import select
import termios
import tty

# ... (TOOLS definition is unchanged) ...
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
    # ... (__init__ and extract_summary are unchanged) ...
    def __init__(self, base_url: str = "http://localhost:10000", token: str | None = None):
        self.base_url = os.getenv("API_BASE", base_url)
        self.token = token or os.environ.get("API_KEY")
        if not self.token:
            raise ValueError(
                "API token must be provided either directly or via OPENAI_API_KEY environment variable"
            )

        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        self.chat_dir = Path.cwd() / "localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)

        try:
            script_dir = Path(__file__).resolve().parent
            system_prompt_path = script_dir / "systemPrompt"
            with open(system_prompt_path, "r", encoding="utf-8") as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            print(
                "Warning: 'systemPrompt' file not found. Using default system prompt.",
                file=sys.stderr,
            )
            system_prompt_content = "You are a helpful assistant."

        self.messages: List[Dict] = [{"role": "system", "content": system_prompt_content}]
        self.summary: Optional[str] = None
        self.tools = TOOLS  # keep handy

    # --------------- helpers --------------- #
    def extract_summary(self, text):
        start_tag, end_tag = "<summary>", "</summary>"
        start_index = text.rfind(start_tag)
        if start_index == -1:
            return None
        start_index += len(start_tag)
        end_index = text.find(end_tag, start_index)
        if end_index == -1:
            return None
        return text[start_index:end_index].strip()

    def _interrupt_listener(self, interrupt_event: threading.Event, stop_event: threading.Event):
        if not sys.stdin.isatty():
            return

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while not stop_event.is_set():
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    char = sys.stdin.read(1)
                    if ord(char) == 9: # Ctrl+I (Tab)
                        interrupt_event.set()
                        break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def _handle_tool_call(self, call: Dict):
        fn_name = call["function"]["name"]
        try:
            args_str = call["function"].get("arguments", "{}") or "{}"
            args = json.loads(args_str)
            script = args.get("script", "")
        except json.JSONDecodeError:
            print(f"\nError: Could not decode arguments for tool {fn_name}.", file=sys.stderr)
            return

        call_id = call["id"]
        
        # This now runs in a normal terminal, so input() works correctly.
        # We re-print the tool call so the user knows what they are confirming.
        print(f"\n[Tool Call: {fn_name}]")
        print("```" + fn_name)
        print(script)
        print("```")
        confirm = input(f"Execute the above '{fn_name}' tool call? [y/N]: ").lower().strip()

        if confirm != "y":
            output = "--- SKIPPED BY USER ---"
        else:
            print("Executing...")
            output = run_bash_script(script) if fn_name == "bash" else run_python_script(script)
            print(output) # This print is also safe now.

        self.messages.append(
            {
                "role": "tool",
                "name": fn_name,
                "tool_call_id": call_id,
                "content": output,
            }
        )

    def send_message(self, message: str):
        self.messages.append({"role": "user", "content": message})

        while True:
            interrupt_event = threading.Event()
            stop_listener_event = threading.Event()
            listener_thread = threading.Thread(
                target=self._interrupt_listener,
                args=(interrupt_event, stop_listener_event),
                daemon=True,
            )

            assistant_text_parts: list[str] = []
            tool_calls_buf: dict[int, dict] = {}
            interrupted = False

            try:
                listener_thread.start()

                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self.headers,
                    json={
                        "model": os.environ.get("LOCAL_OPENAI_API_MODEL"),
                        "messages": self.messages,
                        "tools": self.tools,
                        "tool_choice": "auto",
                        "stream": True,
                    },
                    timeout=120,
                    stream=True,
                )
                response.raise_for_status()

                printed_tool_headers = set()

                def _raw_print(text: str):
                    """A print function that is safe to use in a raw terminal."""
                    sys.stdout.write(text.replace("\n", "\r\n"))
                    sys.stdout.flush()

                _raw_print("\n[Assistant]: ")

                for raw in response.iter_lines(decode_unicode=True):
                    if interrupt_event.is_set():
                        sys.stdout.write("\r\x1b[2K\r\n[Interrupted by user]\r\n")
                        sys.stdout.flush()
                        interrupted = True
                        break

                    if not raw or not raw.startswith("data: "):
                        continue
                    data = raw[6:]
                    if data == "[DONE]":
                        break

                    chunk = json.loads(data)
                    choice = chunk["choices"][0]
                    delta = choice.get("delta", {})

                    if "content" in delta and delta["content"]:
                        txt = delta["content"]
                        assistant_text_parts.append(txt)
                        _raw_print(txt)

                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            index = tc_delta["index"]
                            if index not in tool_calls_buf:
                                tool_calls_buf[index] = {
                                    "id": None, "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc_full = tool_calls_buf[index]
                            if "id" in tc_delta and tc_delta["id"]:
                                tc_full["id"] = tc_delta["id"]
                            if "function" in tc_delta:
                                f_delta = tc_delta["function"]
                                if "name" in f_delta and f_delta["name"]:
                                    tc_full["function"]["name"] = f_delta["name"]
                                if index not in printed_tool_headers and tc_full["function"]["name"]:
                                    _raw_print(f"\n\n[Tool Call: {tc_full['function']['name']}]\n")
                                    printed_tool_headers.add(index)
                                if "arguments" in f_delta:
                                    args_chunk = f_delta["arguments"]
                                    _raw_print(args_chunk)
                                    tc_full["function"]["arguments"] += args_chunk
                
                _raw_print("\n")

            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                print(f"\nError: {e}", file=sys.stderr)
                interrupted = True # Treat errors as an interruption
            finally:
                # CRITICAL: Stop the listener and restore the terminal BEFORE any more interaction.
                stop_listener_event.set()
                if listener_thread.is_alive():
                    listener_thread.join()

            # --- POST-STREAMING ---
            # The terminal is now back in NORMAL mode.
            
            assistant_msg: dict = {"role": "assistant"}
            full_text = "".join(assistant_text_parts).strip()

            if interrupted and full_text:
                full_text += "\n\n--- Interrupted by user ---"

            if full_text:
                assistant_msg["content"] = full_text
                self.summary = self.extract_summary(full_text)

            if tool_calls_buf:
                assistant_msg["tool_calls"] = list(tool_calls_buf.values())

            if full_text or tool_calls_buf:
                self.messages.append(assistant_msg)

            if interrupted:
                return

            if not tool_calls_buf:
                return

            # Now that the terminal is normal, loop through and handle each tool call.
            for tc in assistant_msg["tool_calls"]:
                self._handle_tool_call(tc)

            continue

    # ... (send_context_only, save_chat, load_chat are unchanged) ...
    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        try:
            requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": os.environ.get("LOCAL_OPENAI_API_MODEL"),
                    "messages": self.messages,
                    "stream": False,
                    "max_tokens": 1,
                },
                timeout=120,
            ).raise_for_status()
        except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
            print(f"\nError: Failed to send context to LLM: {e}", file=sys.stderr)
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
        if chat_file.exists():
            with open(chat_file, "r") as f:
                self.messages = json.load(f)
            print(f"Loaded chat: {chat_name}")

            print("\n--- Previous conversation ---")
            for msg in self.messages:
                if msg["role"] == "system":
                    continue
                elif msg["role"] == "user":
                    print(f"\n[You]:\n{msg['content']}")
                elif msg["role"] == "assistant":
                    summary = self.extract_summary(msg.get("content", ""))
                    content_to_print = msg.get("content", "")
                    if summary:
                        content_to_print = content_to_print.replace(
                            f"<summary>{summary}</summary>", ""
                        ).strip()
                    print(f"\n[Assistant]:\n{content_to_print}")
            print("\n--- End of previous conversation ---\n")
        else:
            print(f"Chat file not found: {chat_name}")
