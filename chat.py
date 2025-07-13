#!/usr/bin/env python3

import os
import sys
import json
import argparse
import datetime
import readline
import re
import signal
from pathlib import Path
import requests
from typing import List, Dict
import subprocess


def get_multiline_input(client: 'ChatClient') -> str:
    import termios
    import sys, tty

    print("\n[You]: ", end='', flush=True)
    lines = []
    current_line = []
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        
        while True:
            char = sys.stdin.read(1)
            
            if not char or ord(char) == 4:
                if current_line:
                    lines.append(''.join(current_line))
                print()
                break
                
            elif ord(char) == 3:
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

            elif ord(char) == 5:  # Ctrl+E (ASCII code 5)
                current_line = []
                lines.clear()  # Clear all accumulated lines for this message
                # Clear terminal display and reset prompt
                sys.stdout.write('\r\x1b[2KYou: ')
                sys.stdout.flush()

            elif ord(char) == 24: # Ctrl+X
                sys.exit(0)
                
            elif char == '\r' or char == '\n':
                lines.append(''.join(current_line))
                current_line = []
                sys.stdout.write('\r\n')
                sys.stdout.flush()
                
            elif ord(char) == 127:
                if current_line:
                    current_line.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
                    
            else:
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
            system_prompt_content = "Always include a summary of the conversation within <summary> tags at the end of each of your responses. The summary should be maximum 10 words, and at least 3 words."

        self.messages: List[Dict] = [{"role": "system", "content": system_prompt_content}]
        self.summary = None

    def extract_summary(self, text):
        start = text.rfind('<summary>') + len('<summary>')
        end = text.find('</summary>', start)
        if start == -1 or end == -1:
            return None
        return text[start:end].strip()
 
        
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

            # Find all markdown bash command blocks in the response.
            bash_matches = list(re.finditer(r'```bash\n(.*?)\n```', full_reply, re.DOTALL))
            
            if bash_matches:
                print()
                all_outputs_for_history = ""
                num_commands = len(bash_matches)

                for i, match in enumerate(bash_matches):
                    script_to_run = match.group(1).strip()
                    if not script_to_run:
                        continue

                    print("----------------------------------------")
                    print(f"LLM wants to execute command {i+1} of {num_commands}:\n\n{script_to_run}")
                    print("----------------------------------------")
                    
                    try:
                        confirm = input("Do you want to execute this command? [y/N]: ").lower().strip()
                    except EOFError:
                        confirm = 'n'

                    if confirm == 'y':
                        print("Executing...")
                        output = run_bash_script(script_to_run)
                        print(output)
                        all_outputs_for_history += f"\n\n--- SCRIPT {i+1} OUTPUT ---\n{output}"
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
        """Sends a message to prime the LLM's context without streaming a reply."""
        self.messages.append({"role": "user", "content": message})
        print("\nSending context to LLM...", flush=True)
        
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self.headers,
                json={
                    "model": os.environ.get('LOCAL_OPENAI_API_MODEL'),
                    "messages": self.messages,
                    "stream": False,
                    "max_tokens": 1 # We don't need a response, so ask for the minimum.
                },
                timeout=20 # Give it a reasonable timeout to process.
            )
            response.raise_for_status()
            # We intentionally ignore the response content and do not add an assistant message.
            print("Done.")
            
        except requests.exceptions.RequestException as e:
            print(f"\nError: Failed to send context to LLM: {e}", file=sys.stderr)
            # If the request fails, remove the message we added to keep history consistent.
            self.messages.pop()

    def save_chat(self) -> str:
        summary = self.summary if self.summary else "unnamed_chat"
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        chat_name = f"{timestamp}_{summary.replace(' ', '_')}.json"
        
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
        chats = [chat.name for chat in client.chat_dir.iterdir() if chat.is_file()]
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
                loaded_messages = json.load(f)
                client.messages = loaded_messages.copy()
            print(f"Loaded chat: {args.load}")
            
            print("\nPrevious conversation:")
            for msg in client.messages:
                if msg['role'] == 'system':
                    continue
                elif msg['role'] == 'user':
                    print(f"\nYou: {msg['content']}")
                elif msg['role'] == 'assistant':
                    print(f"  [Assistant]: {msg['content']}\n")
        else:
            print(f"Chat file not found: {args.load}")
            return


    print("Chat started. Press Ctrl+D to submit message, Ctrl+C to exit and save, Ctrl+X to exit without saving.")
    
    def signal_handler(sig, frame):
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")
        exit(0)
    
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
                    # This message informs the LLM that a command was run.
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
