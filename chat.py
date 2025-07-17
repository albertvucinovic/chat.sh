import sys
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from chat_client import ChatClient
from completer import PtkCompleter
from executors import run_bash_script

def main():
    """
    Main function to run the chat application.
    """
    console = Console()
    try:
        client = ChatClient()
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        console.print("Please provide API_KEY, API_MODEL, API_BASE environment variables")
        return

    # --- Key Bindings for prompt-toolkit ---
    kb = KeyBindings()

    # Ctrl+D to submit multiline input
    @kb.add("c-d")
    def _(event):
        event.app.exit(result=event.current_buffer.text)

    # Ctrl+C to exit the application gracefully WHEN AT THE PROMPT
    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    # --- Prompt Session Setup ---
    session = PromptSession(
        "You: ",
        completer=PtkCompleter(client),
        auto_suggest=AutoSuggestFromHistory(),
        key_bindings=kb,
        multiline=True,
        prompt_continuation="... ",
    )

    console.print(
        Panel(
            "Chat started. [bold]Tab[/bold] to autocomplete. [bold]Ctrl+D[/bold] to submit. [bold]Ctrl+C[/bold] to exit or interrupt.",
            title="[bold]Welcome[/bold]",
            border_style="magenta"
        )
    )

    def shutdown():
        """Saves the chat and exits cleanly."""
        console.print("\n\n[bold yellow]Saving chat and exiting...[/bold yellow]")
        saved_path = client.save_chat()
        console.print(f"[green]Chat saved to:[/green] {saved_path}")
        sys.exit(0)

    # --- Main Application Loop ---
    while True:
        try:
            user_input = session.prompt().strip()

            if not user_input:
                continue

            # Handle local one-off bash command (prefix "b ")
            if user_input.startswith("b "):
                console.print("\n[cyan]Executing local command...[/cyan]")
                script_to_run = user_input[2:].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    console.print(Panel(Text(output), title="[bold green]Local Command Output[/bold green]", border_style="green"))
                    context_message = (
                        "User executed a local command.\n"
                        f"Command:\n```bash\n{script_to_run}\n```\n\n"
                        f"Output:\n---\n{output}\n---"
                    )
                    client.send_context_only(context_message)
                else:
                    console.print("[yellow]Empty bash command, skipping.[/yellow]")
                continue

            # Handle load chat command (prefix "o ")
            elif user_input.startswith("o "):
                chat_name = user_input[2:].strip()
                if chat_name:
                    client.load_chat(chat_name)
                else:
                    console.print("[yellow]No chat file specified.[/yellow]")
                continue

            # Send message to the chat client for processing
            client.send_message(user_input)

        except KeyboardInterrupt:
            # This catches Ctrl+C FROM THE PROMPT and exits.
            shutdown()
        except EOFError:
            # Catches Ctrl+D on an empty line to exit
            shutdown()

if __name__ == "__main__":
    main()
