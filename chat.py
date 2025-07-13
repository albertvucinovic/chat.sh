#!/usr/bin/env python3

import os
import sys
import json
import argparse
import datetime
import re
import signal
from pathlib import Path
import requests
from typing import List, Dict, Set, Optional
import subprocess
import termios
import tty
from io import StringIO

class Completer:
    """
    Manages completion state and suggestion generation from history and the filesystem.
    """
    def __init__(self, client: 'ChatClient'):
        self.client = client
        self.suggestions: List[str] = []
        self.current_index = -1
        self.active = False

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        word_regex = re.compile(r'[\w.-]+')
        for message in self.client.messages:
            content = message.get("content", "")
            found_words = word_regex.findall(content.lower())
            words.update(found_words)
        return words

    def _get_words_from_filesystem(self) -> Set[str]:
        """Gets all file and directory names from the current directory."""
        try:
            return set(os.listdir('.'))
        except OSError:
            return set()

    def find_suggestions(self, line: List[str]):
        """
        Generate suggestions based on the word before the cursor.
        The "word" is defined as everything after the last whitespace or delimiter.
        """
        current_text = "".join(line)
        delimiters = ' \t\n`~!@#$%^&*()=+[{]}\\|;:\'",<>/?'
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

        self.suggestions = sorted([
            word for word in all_words if word.lower().startswith(prefix.lower()) and word.lower() != prefix.lower()
        ])
        
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
        self.current_index = (self.current_index - 1 + len(self.suggestions)) % len(self.suggestions)
        return self.suggestions[self.current_index]

    def apply_suggestion(self, current_line: List[str], suggestion: str) -> List[str]:
        """Replaces the current word with the chosen suggestion."""
        current_text = "".join(current_line)
        delimiters = ' \t\n`~!@#$%^&*()=+[{]}\\|;:\'",<>/?'
        word_start_index = 0
        for i in range(len(current_text) - 1, -1, -1):
            if current_text[i] in delimiters:
                word_start_index = i + 1
                break
        
        if os.path.isdir(suggestion):
            suggestion += '/'

        new_line = list(current_text[:word_start_index])
        new_line.extend(list(suggestion))
        return new_line

    def reset(self):
        """Resets the completer state."""
        self.suggestions = []
        self.current_index = -1
        self.active = False

def get_multiline_input(client: 'ChatClient') -> str:
    completer = Completer(client)
    
    CLEAR_ENTIRE_LINE = "\x1b[2K"
    MOVE_UP_1 = "\x1b[A"

    def _clear_suggestions():
        """Resets completer state."""
        if completer.active:
            completer.reset()

    print("\n[You]: ", end='', flush=True)
    lines = []
    current_line = []
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        
        while True:
            char = sys.stdin.read(1)
            
            if not char or ord(char) == 4: # Ctrl+D
                if current_line:
                    lines.append(''.join(current_line))
                print()
                break
                
            elif ord(char) == 3: # Ctrl+C
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                print("\nSaving chat and exiting...")
                if current_line or lines:
                    if current_line:
                        lines.append(''.join(current_line))
                    final_input = '\n'.join(lines)
                    if final_input.strip():
                        client.messages.append({"role": "user", "content": final_input})
                saved_path = client.save_chat()
                print(f"Chat saved to: {saved_path}")
                sys.exit(0)

            elif ord(char) == 5: # Ctrl+E to clear all input
                _clear_suggestions()
                sys.stdout.write('\r' + CLEAR_ENTIRE_LINE)
                for _ in range(len(lines)):
                    sys.stdout.write(MOVE_UP_1)
                    sys.stdout.write(CLEAR_ENTIRE_LINE)
                lines.clear()
                current_line.clear()
                sys.stdout.write("[You]: ")
                sys.stdout.flush()
                continue

            elif char == '\t' or char == '\x1b': # Tab or Escape sequence
                if char == '\x1b':
                    # Check for Shift+Tab: ESC [ Z
                    next_chars = sys.stdin.read(2)
                    if next_chars != '[Z':
                        continue # Ignore other escape sequences
                    is_forward = False
                else:
                    is_forward = True

                if not completer.active:
                    completer.find_suggestions(current_line)
                
                if is_forward:
                    suggestion = completer.next_suggestion()
                else:
                    suggestion = completer.previous_suggestion()

                if suggestion:
                    current_line = completer.apply_suggestion(current_line, suggestion)
                    sys.stdout.write('\r' + CLEAR_ENTIRE_LINE)
                    sys.stdout.write("[You]: " + ''.join(current_line))
                
                sys.stdout.flush()
                continue

            _clear_suggestions()

            if char == '\r' or char == '\n':
                lines.append(''.join(current_line))
                current_line = []
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                
            elif ord(char) == 127: # Backspace
                if current_line:
                    current_line.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
                    
            else:
                if char.isprintable():
                    current_line.append(char)
                    sys.stdout.write(char)
                    sys.stdout.flush()
                
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    
    return '\n'.join(lines)

def run_bash_script(script: str) -> str:
    """Executes a bash script and captures its stdout and stderr."""
    try:
        result = subprocess.run(
            script,
            shell=True,
            executable='/bin/bash',
            capture_output=True,
            text=True,
            timeout=60
        )
        output = ""
        if result.stdout:
            output += f"--- STDOUT ---\n{result.stdout.strip()}\n"
        if result.stderr:
            output += f"--- STDERR ---\n{result.stderr.strip()}\n"
        
        if not output.strip():
            return "--- (No output) ---"
        return output.strip()
    except subprocess.TimeoutExpired:
        return "--- STDERR ---\nError: Command timed out after 60 seconds."
    except Exception as e:
        return f"--- STDERR ---\nError executing command: {e}"

def run_python_script(script: str) -> str:
    """Executes a Python script string and captures its output."""
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_stdout = sys.stdout = StringIO()
    redirected_stderr = sys.stderr = StringIO()
    
    try:
        exec(script, globals())
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        
        output = ""
        stdout_val = redirected_stdout.getvalue().strip()
        stderr_val = redirected_stderr.getvalue().strip()

        if stdout_val:
            output += f"--- STDOUT ---\n{stdout_val}\n"
        if stderr_val:
            output += f"--- STDERR ---\n{stderr_val}\n"
            
        if not output.strip():
            return "--- (No output) ---"
        return output.strip()

    except Exception as e:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        return f"--- STDERR ---\nError executing Python script: {e}"

class ChatClient:
    def __init__(self, base_url: str = "http://localhost:10000", token: str = None):
        self.base_url = base_url
        self.token = token or os.environ.get('LOCAL_OPENAI_API_KEY')
        if not self.token:
            raise ValueError("API token must be provided either directly or via OPENAI_API_KEY environment variable")
        
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        self.chat_dir = Path.cwd() / "localChats"
        self.chat_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            script_dir = Path(__file__).resolve().parent
            system_prompt_path = script_dir / "systemPrompt"
            with open(system_prompt_path, 'r', encoding='utf-8') as f:
                system_prompt_content = f.read()
        except FileNotFoundError:
            print("Warning: 'systemPrompt' file not found. Using default system prompt.", file=sys.stderr)
            system_prompt_content = "You are a helpful assistant."

        self.messages: List[Dict] = [{"role": "system", "content": system_prompt_content}]
        self.summary = None

    def extract_summary(self, text):
        start_tag = '<summary>'
        end_tag = '</summary>'
        start_index = text.rfind(start_tag)
        if start_index == -1:
            return None
        
        start_index += len(start_tag)
        end_index = text.find(end_tag, start_index)
        
        if end_index == -1:
            return None
            
        return text[start_index:end_index].strip()
 
    def send_message(self, message: str) -> str:
        self.messages.append({"role": "user", "content": message})
        
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": os.environ.get('LOCAL_OPENAI_API_MODEL'),
                    "messages": self.messages,
                    "stream": True
                },
                stream=True
            )
            response.raise_for_status()
            
            collected_chunks = []
            for chunk in response.iter_lines():
                if chunk:
                    chunk = chunk.decode('utf-8')
                    if chunk.startswith('data: '):
                        chunk = chunk[6:]
                        if chunk != '[DONE]':
                            chunk_data = json.loads(chunk)
                            choices = chunk_data.get('choices', [])
                            if choices and 'content' in choices[0].get('delta', {}):
                                content = choices[0]['delta']['content']
                                print(content, end='', flush=True)
                                collected_chunks.append(content)
                                    
            print()
            full_reply = ''.join(collected_chunks)
            final_reply_content = full_reply

            tool_matches = []
            bash_regex = r'```bash\n(.*?)\n```'
            python_regex = r'```python\n(.*?)\n```'
            
            for match in re.finditer(bash_regex, full_reply, re.DOTALL):
                tool_matches.append(('bash', match.group(1).strip()))
            
            for match in re.finditer(python_regex, full_reply, re.DOTALL):
                tool_matches.append(('python', match.group(1).strip()))

            if tool_matches:
                print()
                all_outputs_for_history = ""
                num_commands = len(tool_matches)

                for i, (tool_type, script_to_run) in enumerate(tool_matches):
                    if not script_to_run:
                        continue

                    print("----------------------------------------")
                    print(f"LLM wants to execute the following {tool_type} script ({i+1} of {num_commands}):\n")
                    print(script_to_run)
                    print("----------------------------------------")
                    
                    try:
                        confirm = input("Do you want to execute this script? [y/N]: ").lower().strip()
                    except EOFError:
                        confirm = 'n'

                    if confirm == 'y':
                        print("Executing...")
                        output = ""
                        if tool_type == 'bash':
                            output = run_bash_script(script_to_run)
                        elif tool_type == 'python':
                            output = run_python_script(script_to_run)
                        
                        print(output)
                        all_outputs_for_history += f"\n\n--- SCRIPT {i+1} ({tool_type}) OUTPUT ---\n{output}"
                    else:
                        print("Execution skipped.")
                
                if all_outputs_for_history:
                    final_reply_content += all_outputs_for_history
            
            self.summary = self.extract_summary(final_reply_content)
            self.messages.append({"role": "assistant", "content": final_reply_content})
            return final_reply_content
            
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
            return ""

    def send_context_only(self, message: str):
        self.messages.append({"role": "user", "content": message})
        
        try:
            requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": os.environ.get('LOCAL_OPENAI_API_MODEL'),
                    "messages": self.messages,
                    "stream": False,
                    "max_tokens": 1
                },
                timeout=20
            ).raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"\nError: Failed to send context to LLM: {e}", file=sys.stderr)
            self.messages.pop()

    def save_chat(self) -> str:
        summary = self.summary if self.summary else "unnamed_chat"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_summary = re.sub(r'[^\w\-]', '_', summary)
        chat_name = f"{timestamp}_{safe_summary}.json"
        
        file_path = self.chat_dir / chat_name
        with open(file_path, 'w') as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)

def main():
    parser = argparse.ArgumentParser(description="CLI Chat Client for Local OpenAI API")
    parser.add_argument('--load', help='Load a previous chat file')
    parser.add_argument('--list', action='store_true', help='List available chat files')
    parser.add_argument('--token', help='OpenAI API token')
    parser.add_argument('--url', default='http://localhost:10000', help='API base URL')
    args = parser.parse_args()

    try:
        client = ChatClient(base_url=args.url, token=args.token)
    except ValueError as e:
        print(f"Error: {e}")
        print("Please provide an API token either via --token or by setting the OPENAI_API_KEY environment variable")
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
            with open(chat_file, 'r') as f:
                client.messages = json.load(f)
            print(f"Loaded chat: {args.load}")
            
            print("\n--- Previous conversation ---")
            for msg in client.messages:
                if msg['role'] == 'system':
                    continue
                elif msg['role'] == 'user':
                    print(f"\n[You]:\n{msg['content']}")
                elif msg['role'] == 'assistant':
                    summary = client.extract_summary(msg['content'])
                    content_to_print = msg['content']
                    if summary:
                        content_to_print = content_to_print.replace(f"<summary>{summary}</summary>", "").strip()
                    print(f"\n[Assistant]:\n{content_to_print}")
            print("\n--- End of previous conversation ---\n")
        else:
            print(f"Chat file not found: {args.load}")
            return

    print("Chat started. Press Tab to autocomplete. Press Ctrl+D to submit. Press Ctrl+C to exit and save. Press Ctrl+E to clear input.")
    
    def signal_handler(sig, frame):
        print("\n\nSaving chat and exiting...")
        fd = sys.stdin.fileno()
        try:
            original_settings = termios.tcgetattr(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)
        except:
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

            bash_command = "b "
            if user_input.startswith(bash_command):
                print("\nExecuting local command...")
                script_to_run = user_input[len(bash_command):].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    print(output)
                    context_message = (
                        f"User executed a local command.\n"
                        f"Command:\n```bash\n{script_to_run}\n```\n\n"
                        f"Output:\n---\n{output}\n---"
                    )
                    client.send_context_only(context_message)
                    continue
                else:
                    print("Empty bash command, skipping.")
                    continue
            
            print("\n[Assistant]:", end=' ')
            client.send_message(user_input)
                
    except EOFError:
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")

if __name__ == "__main__":
    main()
