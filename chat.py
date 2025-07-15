#!/usr/bin/env python3
"""
CLI chat client that talks to a locally-hosted OpenAI-compatible endpoint.

Key change 2025-07-15:
--------------------------------------------------
The client now relies on OpenAI’s *native* tool-calling protocol instead
of parsing fenced code blocks. It also streams tool code to the console
as it's generated.

Two tools are exposed to the model:

  • bash(script: str)   – run a shell script via /bin/bash
  • python(script: str) – exec a Python snippet in-process

Everything else (auto-completion, local `b ` prefix, Ctrl shortcuts, file
saving, etc.) is untouched.
--------------------------------------------------
"""

import os
import sys
import json
import argparse
import datetime
import re
import signal
import textwrap
from pathlib import Path
import requests
from typing import List, Dict, Set, Optional
import subprocess
import termios
import tty
from io import StringIO

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


# ============================== Completer ============================== #
class Completer:
    """
    Manages completion state and suggestion generation from history and
    the filesystem.
    """

    def __init__(self, client: "ChatClient"):
        self.client = client
        self.suggestions: List[str] = []
        self.current_index = -1
        self.active = False

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        word_regex = re.compile(r"[\w.-]+")
        for message in self.client.messages:
            content = message.get("content", "")
            found_words = word_regex.findall(content.lower())
            words.update(found_words)
        return words

    def _get_words_from_filesystem(self) -> Set[str]:
        """Gets all file and directory names from the current directory."""
        try:
            return set(os.listdir("."))
        except OSError:
            return set()

    def find_suggestions(self, line: List[str]):
        """
        Generate suggestions based on the word before the cursor.
        The "word" is defined as everything after the last whitespace or delimiter.
        """
        current_text = "".join(line)
        delimiters = " \t\n`~!@#$%^&*()=+[{]}\\|;:'\",<>/?"
        word_start_index = 0
        for i in range(len(current_text) - 1, -1, -1):
            if current_text[i] in delimiters:
                word_start_index = i + 1
                break

        prefix = current_text[word_start_index:]

        if not prefix:
            self.reset()
            return

        history_words = self._get_words_from_history()
        fs_words = self._get_words_from_filesystem()
        all_words = history_words.union(fs_words)

        self.suggestions = sorted(
            [
                word
                for word in all_words
                if word.lower().startswith(prefix.lower())
                and word.lower() != prefix.lower()
            ]
        )

        if self.suggestions:
            self.active = True
            self.current_index = -1
        else:
            self.reset()

    def next_suggestion(self) -> Optional[str]:
        """Cycles to the next suggestion."""
        if not self.suggestions:
            return None
        self.current_index = (self.current_index + 1) % len(self.suggestions)
        return self.suggestions[self.current_index]

    def previous_suggestion(self) -> Optional[str]:
        """Cycles to the previous suggestion."""
        if not self.suggestions:
            return None
        self.current_index = (self.current_index - 1 + len(self.suggestions)) % len(
            self.suggestions
        )
        return self.suggestions[self.current_index]

    def apply_suggestion(self, current_line: List[str], suggestion: str) -> List[str]:
        """Replaces the current word with the chosen suggestion."""
        current_text = "".join(current_line)
        delimiters = " \t\n`~!@#$%^&*()=+[{]}\\|;:'\",<>/?"
        word_start_index = 0
        for i in range(len(current_text) - 1, -1, -1):
            if current_text[i] in delimiters:
                word_start_index = i + 1
                break

        if os.path.isdir(suggestion):
            suggestion += "/"

        new_line = list(current_text[:word_start_index])
        new_line.extend(list(suggestion))
        return new_line

    def reset(self):
        """Resets the completer state."""
        self.suggestions = []
        self.current_index = -1
        self.active = False


# ======================== Interactive input ============================ #
def get_multiline_input(client: "ChatClient") -> str:
    completer = Completer(client)

    CLEAR_ENTIRE_LINE = "\x1b[2K"
    MOVE_UP_1 = "\x1b[A"

    def _clear_suggestions():
        """Resets completer state."""
        if completer.active:
            completer.reset()

    print("\n[You]: ", end="", flush=True)
    lines: List[str] = []
    current_line: List[str] = []

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())

        while True:
            char = sys.stdin.read(1)

            # Ctrl+D – submit
            if not char or ord(char) == 4:
                if current_line:
                    lines.append("".join(current_line))
                print()
                break

            # Ctrl+C – save & quit
            elif ord(char) == 3:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                print("\nSaving chat and exiting...")
                if current_line or lines:
                    if current_line:
                        lines.append("".join(current_line))
                    final_input = "\n".join(lines)
                    if final_input.strip():
                        client.messages.append({"role": "user", "content": final_input})
                saved_path = client.save_chat()
                print(f"Chat saved to: {saved_path}")
                sys.exit(0)

            # Ctrl+E – clear
            elif ord(char) == 5:
                _clear_suggestions()
                sys.stdout.write("\r" + CLEAR_ENTIRE_LINE)
                for _ in range(len(lines)):
                    sys.stdout.write(MOVE_UP_1)
                    sys.stdout.write(CLEAR_ENTIRE_LINE)
                lines.clear()
                current_line.clear()
                sys.stdout.write("[You]: ")
                sys.stdout.flush()
                continue

            # Tab or Shift+Tab (Esc [ Z)
            elif char == "\t" or char == "\x1b":
                if char == "\x1b":
                    next_chars = sys.stdin.read(2)
                    if next_chars != "[Z":
                        continue
                    is_forward = False
                else:
                    is_forward = True

                if not completer.active:
                    completer.find_suggestions(current_line)

                suggestion = (
                    completer.next_suggestion()
                    if is_forward
                    else completer.previous_suggestion()
                )

                if suggestion:
                    current_line = completer.apply_suggestion(current_line, suggestion)
                    sys.stdout.write("\r" + CLEAR_ENTIRE_LINE)
                    sys.stdout.write("[You]: " + "".join(current_line))

                sys.stdout.flush()
                continue

            _clear_suggestions()

            if char in ("\r", "\n"):
                lines.append("".join(current_line))
                current_line = []
                sys.stdout.write("\r\n")
                sys.stdout.flush()

            # Backspace
            elif ord(char) == 127:
                if current_line:
                    current_line.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()

            else:
                if char.isprintable():
                    current_line.append(char)
                    sys.stdout.write(char)
                    sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return "\n".join(lines)


# ============================== Executors ============================== #
def run_bash_script(script: str) -> str:
    """Executes a bash script and captures its stdout and stderr."""
    try:
        result = subprocess.run(
            script,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = ""
        if result.stdout:
            output += f"--- STDOUT ---\n{result.stdout.strip()}\n"
        if result.stderr:
            output += f"--- STDERR ---\n{result.stderr.strip()}\n"

        return output.strip() or "--- (No output) ---"
    except subprocess.TimeoutExpired:
        return "--- STDERR ---\nError: Command timed out after 60 seconds."
    except Exception as e:
        return f"--- STDERR ---\nError executing command: {e}"


def run_python_script(script: str) -> str:
    """Executes a Python script string and captures its output."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    redirected_stdout = sys.stdout = StringIO()
    redirected_stderr = sys.stderr = StringIO()

    try:
        exec(script, globals())
        sys.stdout, sys.stderr = old_stdout, old_stderr

        output = ""
        stdout_val = redirected_stdout.getvalue().strip()
        stderr_val = redirected_stderr.getvalue().strip()

        if stdout_val:
            output += f"--- STDOUT ---\n{stdout_val}\n"
        if stderr_val:
            output += f"--- STDERR ---\n{stderr_val}\n"

        return output.strip() or "--- (No output) ---"
    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        return f"--- STDERR ---\nError executing Python script: {e}"


# ============================== Chat client ============================ #
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
    # Send a user message – stream assistant deltas, handle tools
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
        safe_summary = re.sub(r"[^\w\-]", "_", summary)
        chat_name = f"{timestamp}_{safe_summary}.json"

        file_path = self.chat_dir / chat_name
        with open(file_path, "w") as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)


# ============================== CLI entry ============================== #
def main():
    parser = argparse.ArgumentParser(description="CLI Chat Client for Local OpenAI API")
    parser.add_argument("--load", help="Load a previous chat file")
    parser.add_argument("--list", action="store_true", help="List available chat files")
    parser.add_argument("--token", help="OpenAI API token")
    parser.add_argument("--url", default="http://localhost:10000", help="API base URL")
    args = parser.parse_args()

    try:
        client = ChatClient(base_url=args.url, token=args.token)
    except ValueError as e:
        print(f"Error: {e}")
        print(
            "Please provide an API token either via --token or by setting the OPENAI_API_KEY environment variable"
        )
        return

    if args.list:
        chats = sorted([chat.name for chat in client.chat_dir.iterdir() if chat.is_file()])
        if chats:
            print("Available chats:")
            for chat in chats:
                print(f"  {chat}")
        else:
            print("No saved chats found.")
        return

    if args.load:
        chat_file = client.chat_dir / args.load
        if chat_file.exists():
            with open(chat_file, "r") as f:
                client.messages = json.load(f)
            print(f"Loaded chat: {args.load}")

            print("\n--- Previous conversation ---")
            for msg in client.messages:
                if msg["role"] == "system":
                    continue
                elif msg["role"] == "user":
                    print(f"\n[You]:\n{msg['content']}")
                elif msg["role"] == "assistant":
                    summary = client.extract_summary(msg.get("content", ""))
                    content_to_print = msg.get("content", "")
                    if summary:
                        content_to_print = content_to_print.replace(
                            f"<summary>{summary}</summary>", ""
                        ).strip()
                    print(f"\n[Assistant]:\n{content_to_print}")
            print("\n--- End of previous conversation ---\n")
        else:
            print(f"Chat file not found: {args.load}")
            return

    print(
        "Chat started. Press Tab to autocomplete. "
        "Press Ctrl+D to submit. Press Ctrl+C to exit and save. Press Ctrl+E to clear input."
    )

    def signal_handler(sig, frame):
        print("\n\nSaving chat and exiting...")
        fd = sys.stdin.fileno()
        try:
            original_settings = termios.tcgetattr(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)
        except Exception:
            pass
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            user_input = get_multiline_input(client).strip()

            if not user_input:
                continue

            # Local one-off bash command (prefix "b ")
            if user_input.startswith("b "):
                print("\nExecuting local command...")
                script_to_run = user_input[2:].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    print(output)
                    context_message = (
                        "User executed a local command.\n"
                        f"Command:\n```bash\n{script_to_run}\n```\n\n"
                        f"Output:\n---\n{output}\n---"
                    )
                    client.send_context_only(context_message)
                else:
                    print("Empty bash command, skipping.")
                continue

            # Normal chat flow (may trigger tool calls)
            client.send_message(user_input)

    except EOFError:
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")


if __name__ == "__main__":
    main()
