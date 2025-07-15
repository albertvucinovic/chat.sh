import os
import sys
import json
import datetime
import re
import requests
from pathlib import Path
from typing import List, Dict, Optional
from executors import run_bash_script, run_python_script

# ---------------------------------------------------------------------
# Native tool schema we expose to the LLM
# ---------------------------------------------------------------------
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
    def __init__(self, base_url: str = "http://localhost:10000", token: str | None = None):
        self.base_url = os.getenv("OPENAI_API_BASE", base_url)
        self.token = token or os.environ.get("LOCAL_OPENAI_API_KEY")
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

    # ------------------------------------------------------------
    # Helper: run one tool call requested by the model
    # ------------------------------------------------------------
    def _handle_tool_call(self, call: Dict):
        # --- MODIFIED: Simplified to only handle execution after streaming ---
        fn_name = call["function"]["name"]
        # FIX: Handle empty arguments string to prevent JSONDecodeError
        args_str = call["function"].get("arguments", "{}") or "{}"
        args = json.loads(args_str)
        script = args.get("script", "")
        call_id = call["id"]

        # The user has already seen the code stream in. Just ask for confirmation.
        confirm = input(f"\nExecute the above '{fn_name}' tool call? [y/N]: ").lower().strip()

        if confirm != "y":
            output = "--- SKIPPED BY USER ---"
        else:
            print("Executing...")
            output = run_bash_script(script) if fn_name == "bash" else run_python_script(script)
            print(output)

        # Send the result back so the model can see it
        self.messages.append(
            {
                "role": "tool",
                "name": fn_name,
                "tool_call_id": call_id,
                "content": output,
            }
        )

    # ------------------------------------------------------------
    # Send a user message ÃÂ¢ stream assistant deltas, handle tools
    # ------------------------------------------------------------
    def send_message(self, message: str):
        self.messages.append({"role": "user", "content": message})

        while True:
            try:
                mistral_model = "mistral" in os.environ.get("LOCAL_OPENAI_API_MODEL", "").lower()
                should_stream   = not mistral_model          # disable streaming for Mistral
                should_stream = True
                response = requests.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers=self.headers,
                    json={
                        "model": os.environ.get("LOCAL_OPENAI_API_MODEL"),
                        "messages": self.messages,
                        "tools": self.tools,
                        "tool_choice": "auto",
                        "stream": should_stream,
                    },
                    timeout=120,
                    stream=True,
                )
                response.raise_for_status()

                assistant_text_parts: list[str] = []
                # --- MODIFICATION: The buffer is now keyed by the tool's index (int) ---
                tool_calls_buf: dict[int, dict] = {}
                printed_tool_headers = set()

                print("\n[Assistant]: ", end="", flush=True)

                for raw in response.iter_lines(decode_unicode=True):
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
                        print(txt, end="", flush=True)

                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            # --- MODIFICATION: Use index as the primary identifier ---
                            index = tc_delta["index"]

                            # Initialize the buffer for this index if it's the first time we see it
                            if index not in tool_calls_buf:
                                tool_calls_buf[index] = {
                                    "id": None,
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }

                            # Safely get the full tool call object using its index
                            tc_full = tool_calls_buf[index]

                            # Capture the ID when it arrives
                            if "id" in tc_delta and tc_delta["id"]:
                                tc_full["id"] = tc_delta["id"]

                            if "function" in tc_delta:
                                f_delta = tc_delta["function"]
                                if "name" in f_delta and f_delta["name"]:
                                    tc_full["function"]["name"] = f_delta["name"]

                                # Live display logic (now keyed by index)
                                if index not in printed_tool_headers and tc_full["function"]["name"]:
                                    print(f"\n\n[Tool Call: {tc_full['function']['name']}]\n")
                                    printed_tool_headers.add(index)

                                if "arguments" in f_delta:
                                    args_chunk = f_delta["arguments"]
                                    print(args_chunk, end="", flush=True)
                                    tc_full["function"]["arguments"] += args_chunk

                print()

                assistant_msg: dict = {"role": "assistant"}

                full_text = "".join(assistant_text_parts).strip()
                if full_text:
                    assistant_msg["content"] = full_text
                    self.summary = self.extract_summary(full_text)

                if tool_calls_buf:
                    # Convert the dict of tool calls back to a list for the message
                    assistant_msg["tool_calls"] = list(tool_calls_buf.values())

                # It's possible to get tool calls without any text content
                if full_text or tool_calls_buf:
                    self.messages.append(assistant_msg)

                if not tool_calls_buf:
                    return

                for tc in assistant_msg["tool_calls"]:
                    self._handle_tool_call(tc)

                continue

            except requests.exceptions.RequestException as e:
                print(f"\nError: {e}", file=sys.stderr)
                return

    # --------------- lightweight context push --------------- #
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
        except requests.exceptions.RequestException as e:
            print(f"\nError: Failed to send context to LLM: {e}", file=sys.stderr)
            self.messages.pop()

    # --------------- persistence --------------- #
    def save_chat(self) -> str:
        summary = self.summary if self.summary else "unnamed_chat"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_summary = re.sub(r"[^\w-]", "_", summary)
        chat_name = f"{timestamp}_{safe_summary}.json"

        file_path = self.chat_dir / chat_name
        with open(file_path, "w") as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)

    # --------------- load chat --------------- #
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
