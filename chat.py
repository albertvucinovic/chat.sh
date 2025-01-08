#!/usr/bin/env python3

import os
import json
import argparse
import datetime
import readline
from pathlib import Path
import requests
from typing import List, Dict

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
        self.messages: List[Dict] = []
        
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
            
            # Initialize variables for streaming
            collected_chunks = []
            collected_messages = []
            
            # Process the streaming response
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
            self.messages.append({"role": "assistant", "content": full_reply})
            return full_reply
            
        except requests.exceptions.RequestException as e:
            print(f"Error: {e}")
            return ""

    def save_chat(self, chat_name: str = None) -> str:
        if not chat_name:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            chat_name = f"chat_{timestamp}.json"
        
        file_path = self.chat_dir / chat_name
        with open(file_path, 'w') as f:
            json.dump(self.messages, f, indent=2)
        return str(file_path)

    def load_chat(self, chat_name: str) -> bool:
        file_path = self.chat_dir / chat_name
        try:
            with open(file_path, 'r') as f:
                self.messages = json.load(f)
            return True
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def list_chats(self) -> List[str]:
        return [f.name for f in self.chat_dir.glob("*.json")]

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
        chats = client.list_chats()
        if chats:
            print("Available chats:")
            for chat in chats:
                print(f"  {chat}")
        else:
            print("No saved chats found.")
        return

    if args.load:
        if client.load_chat(args.load):
            print(f"Loaded chat: {args.load}")
        else:
            print(f"Failed to load chat: {args.load}")
            return

    print("Chat started. Type 'exit' to quit, 'save' to save the conversation.")
    try:
        while True:
            user_input = input("\nYou: ").strip()
            
            if user_input.lower() == 'exit':
                save = input("Save chat before exiting? (y/n): ").lower()
                if save == 'y':
                    filename = input("Enter filename (leave empty for timestamp): ").strip()
                    saved_path = client.save_chat(filename if filename else None)
                    print(f"Chat saved to: {saved_path}")
                break
                
            elif user_input.lower() == 'save':
                filename = input("Enter filename (leave empty for timestamp): ").strip()
                saved_path = client.save_chat(filename if filename else None)
                print(f"Chat saved to: {saved_path}")
                continue
                
            elif user_input:
                print("\nAssistant:", end=' ')
                client.send_message(user_input)

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
