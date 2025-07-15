import os
import sys
import json
import datetime
import re
import requests
import uuid
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
    def __init__(self):
        self.base_url = os.getenv("API_BASE")
        self.token = os.environ.get("API_KEY")
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
        system_prompt_content += "\n\nYou are using this model: "+ str(os.environ.get("API_MODEL"))

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
                    choices = chunk.get("choices")
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # Safely handle content
                    if delta.get("content"):
                        txt = delta["content"]
                        assistant_text_parts.append(txt)
                        _raw_print(txt)

                    # Safely handle standard tool calls
                    tool_calls_chunk = delta.get("tool_calls")
                    if tool_calls_chunk:
                        for index, tc_delta in enumerate(tool_calls_chunk):
                            buffer_index = tc_delta.get("index", index)
                            if buffer_index not in tool_calls_buf:
                                tool_calls_buf[buffer_index] = {"id": f"call_{uuid.uuid4().hex[:10]}", "type": "function", "function": {"name": "", "arguments": ""}}
                            tc_full = tool_calls_buf[buffer_index]
                            if tc_delta.get("id"): tc_full["id"] = tc_delta["id"]
                            f_delta = tc_delta.get("function", {})
                            newly_received_args = f_delta.get("arguments", "")
                            name_was_known = bool(tc_full["function"]["name"])
                            if f_delta.get("name"): tc_full["function"]["name"] = f_delta["name"]
                            if newly_received_args: tc_full["function"]["arguments"] += newly_received_args
                            name_just_appeared = tc_full["function"]["name"] and not name_was_known
                            if name_just_appeared: _raw_print(f"\n\n[Tool Call: {tc_full['function']['name']}]\n")
                            if newly_received_args:
                                is_all_in_one_chunk = name_just_appeared and tc_full['function']['arguments'] == newly_received_args
                                if is_all_in_one_chunk:
                                    try:
                                        args_dict = json.loads(newly_received_args)
                                        script_content = args_dict.get('script', '')
                                        _raw_print("```" + tc_full['function']['name'] + "\n" + script_content + "\n```")
                                    except Exception:
                                        _raw_print(newly_received_args)
                                else:
                                    _raw_print(newly_received_args)
                _raw_print("\n")

            except (requests.exceptions.RequestException, KeyboardInterrupt) as e:
                print(f"\nError: {e}", file=sys.stderr)
                interrupted = True
            finally:
                stop_listener_event.set()
                if listener_thread.is_alive():
                    listener_thread.join()

            # --- POST-STREAMING ---
            assistant_msg: dict = {"role": "assistant"}
            full_text = "".join(assistant_text_parts).strip()

            ### START of new logic ###
            # This block handles models that stream tool calls as a JSON string in the 'content' field.
            # It only runs if the standard 'tool_calls' field was not found in the stream.
            if not tool_calls_buf and full_text.strip().startswith('{'):
                try:
                    potential_tool_json = json.loads(full_text)
                    if isinstance(potential_tool_json, dict) and "tool_calls" in potential_tool_json:
                        # This is a "JSON-in-Content" tool call.
                        parsed_tool_calls = potential_tool_json["tool_calls"]
                        
                        # Clear the ugly raw JSON that was printed to the screen.
                        num_lines = full_text.count('\n') + 2
                        sys.stdout.write(f"\r\x1b[{num_lines}A\r\x1b[J")
                        sys.stdout.flush()
                        
                        _raw_print("\n[Assistant]: ")
                        
                        for i, malformed_tc in enumerate(parsed_tool_calls):
                            # --- Transformation Step ---
                            # Read from the non-standard flat structure
                            tc_name = malformed_tc.get("name", "unknown_tool")
                            tc_args_obj = malformed_tc.get("arguments", {})

                            # Build the standard, compliant tool call object that the rest of the script expects
                            standard_tc = {
                                "id": malformed_tc.get("id", f"call_{uuid.uuid4().hex[:10]}"),
                                "type": "function",
                                "function": {
                                    "name": tc_name,
                                    "arguments": json.dumps(tc_args_obj) # Convert args dict back to a JSON string
                                }
                            }

                            # --- Printing Step (uses data we just extracted) ---
                            _raw_print(f"\n[Tool Call: {tc_name}]\n")
                            try:
                                script = tc_args_obj.get('script', '') # Get script from the args dict
                                _raw_print("```" + tc_name + "\n" + script + "\n```\n")
                            except Exception:
                                _raw_print(f"Could not parse arguments: {tc_args_obj}\n")
                            
                            # --- Buffering Step (stores the *compliant* object) ---
                            tool_calls_buf[i] = standard_tc

                        # Nullify the full_text so it's not processed as a regular message.
                        full_text = ""
                except json.JSONDecodeError:
                    # It looked like JSON, but wasn't. Treat as regular text.
                    pass
            ### END of new logic ###

            if interrupted and full_text:
                full_text += "\n\n--- Interrupted by user ---"

            if full_text:
                assistant_msg["content"] = full_text
                self.summary = self.extract_summary(full_text)

            if tool_calls_buf:
                # Ensure all tool calls have a valid ID before saving
                for tc in tool_calls_buf.values():
                    if not tc.get("id"):
                        tc["id"] = f"call_{uuid.uuid4().hex[:10]}"
                assistant_msg["tool_calls"] = list(tool_calls_buf.values())

            if assistant_msg.get("content") or assistant_msg.get("tool_calls"):
                self.messages.append(assistant_msg)

            if interrupted:
                return

            if not tool_calls_buf:
                return

            for tc in assistant_msg.get("tool_calls", []):
                self._handle_tool_call(tc)

            continue

    # ... (send_context_only, save_chat, load_chat are unchanged) ...
    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        try:
            requests.post(
                f"{self.base_url}",
                headers=self.headers,
                json={
                    "model": os.environ.get("API_MODEL"),
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
