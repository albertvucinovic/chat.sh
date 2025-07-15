import argparse
import signal
import sys
from chat_client import ChatClient
from input_handler import get_multiline_input
from executors import run_bash_script

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
        client.load_chat(args.load)
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

            # Load chat command (prefix "o ")
            elif user_input.startswith("o "):
                chat_name = user_input[2:].strip()
                if chat_name:
                    client.load_chat(chat_name)
                else:
                    print("No chat name provided.")
                continue

            # Normal chat flow (may trigger tool calls)
            client.send_message(user_input)

    except EOFError:
        print("\nSaving chat and exiting...")
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")

if __name__ == "__main__":
    main()
