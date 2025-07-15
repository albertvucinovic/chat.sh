import signal
import sys
import termios
from chat_client import ChatClient
from input_handler import get_multiline_input
from executors import run_bash_script

def main():
    try:
        client = ChatClient()
    except ValueError as e:
        print(f"Error: {e}")
        print("Please provide API_KEY, API_MODEL, API_BASE environment variables")
        return

    print(
        "Chat started. Press Tab to autocomplete. Press Ctrl+I to interrupt generation.\n"
        "Press Ctrl+D to submit. Press Ctrl+C to exit and save. Press Ctrl+E to clear input."
    )

    # --- GLOBAL CTRL+C HANDLER ---
    # This handler ensures that Ctrl+C always triggers a clean exit.
    def signal_handler(sig, frame):
        print("\n\nSaving chat and exiting...")
        # Attempt to restore terminal settings as a failsafe
        try:
            fd = sys.stdin.fileno()
            original_settings = termios.tcgetattr(fd)
            termios.tcsetattr(fd, termios.TCSADRAIN, original_settings)
        except Exception:
            pass # Ignore if it fails (e.g., not a TTY)
        saved_path = client.save_chat()
        print(f"Chat saved to: {saved_path}")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        while True:
            # get_multiline_input now handles its own terminal state and Ctrl+C while typing.
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
                    print("No chat file specified.")
                continue

            client.send_message(user_input)
    except EOFError:
        # This handles Ctrl+D to exit
        signal_handler(None, None)

if __name__ == "__main__":
    main()
