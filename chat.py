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


def get_multiline_input(client: 'ChatClient') -> str:
    import termios
    import sys, tty

    print("\nYou: ", end='', flush=True)
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
        self.messages: List[Dict] = [{"role": "system", "content": "Always include a summary of the conversation within <summary> tags at the end of your response."}]
        self.summary = None
        
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
                        chunk = chunk[6:]  # Remove 'data: ' prefix
                        if chunk != '[DONE]':
                            chunk_data = json.loads(chunk)
                            choices = chunk_data.get('choices', [])
                            if choices:
                                delta = choices[0].get('delta', {})
                                if 'content' in delta:
                                    content = delta['content']
                                    print(content, end='', flush=True)
                                    collected_chunks.append(content)
                                    
            print()  # New line after response
            full_reply = ''.join(collected_chunks)
            
            # Extract summary from the response
            summary_start = full_reply.find('<summary>')
            if summary_start != -1:
                summary_end = full_reply.find('</summary>', summary_start)
                if summary_end != -1:
                    self.summary = full_reply[summary_start+9 : summary_end]
                    main_reply = full_reply[:summary_start]
                else:
                    main_reply = full_reply
                    self.summary = None
            else:
                main_reply = full_reply
                self.summary = None
                
            self.messages.append({"role": "assistant", "content": main_reply})
            return main_reply
            
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
            return ""

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
        else:
            print(f"Chat file not found: {args.load}")
            return

    print("Chat started. Press Ctrl+D to submit message, Ctrl+C to exit.")
    
    def signal_handler(sig, frame):
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")
        exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            user_input = get_multiline_input(client).strip()
            
            if user_input:
                print("\nAssistant:", end=' ')
                client.send_message(user_input)
                
    except EOFError:
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")

if __name__ == "__main__":
    main()

