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
        console.print("Please provide necessary API environment variables.")
        return

    # --- Dynamic Prompt Setup ---
    def get_prompt_message():
        """Returns the prompt string based on border state."""
        return "[You]: " if client.borders_enabled else "You: "

    def get_continuation_message():
        """Returns the continuation prompt string based on border state."""
        return "[...] " if client.borders_enabled else "... "

    # --- Prompt Session Setup ---
    session = PromptSession(
        message=get_prompt_message,
        completer=PtkCompleter(client),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=True,
        prompt_continuation=get_continuation_message,
    )

    # --- Key Bindings for prompt-toolkit ---
    kb = KeyBindings()

    @kb.add("c-d")
    def _(event):
        event.app.exit(result=event.current_buffer.text)

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-e")
    def _(event):
        """Clears the current input buffer."""
        event.current_buffer.reset()

    @kb.add("c-b")
    def _(event):
        """Toggles UI borders and prints a status message above the prompt."""
        client.toggle_borders()
        session.prompt_continuation = get_continuation_message()

    session.key_bindings = kb

    console.print(
        Panel(
            "Chat started. [bold]Tab[/bold] to autocomplete. [bold]Ctrl+D[/bold] to submit.\n"
            "[bold]Ctrl+B[/bold] to toggle borders. [bold]Ctrl+E[/bold] to clear input. [bold]Ctrl+C[/bold] to exit.",
            title="[bold]Welcome[/bold]",
            border_style=client.get_border_style("magenta")
        )
    )

    def shutdown():
        """Saves the chat and exits cleanly."""
        console.print(
            "\n\n[bold yellow]Saving chat and exiting...[/bold yellow]")
        saved_path = client.save_chat()
        console.print(f"[green]Chat saved to:[/green] {saved_path}")
        sys.exit(0)

    # --- Main Application Loop ---
    while True:
        try:
            user_input = session.prompt().strip()

            if not user_input:
                continue

            if user_input.startswith("b "):
                console.print("\n[cyan]Executing local command...[/cyan]")
                script_to_run = user_input[2:].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    output_renderable = Text(output)
                    if client.borders_enabled:
                        console.print(Panel(
                            output_renderable, title="[bold green]Local Command Output[/bold green]", border_style="green"))
                    else:
                        console.print(output_renderable)

                    context_message = (
                        "User executed a local command.\\n"
                        f"Command:\\n```bash\\n{script_to_run}\\n```\\n\\n"
                        f"Output:\\n---\\n{output}\\n---"
                    )
                    client.send_context_only(context_message)
                else:
                    console.print(
                        "[yellow]Empty bash command, skipping.[/yellow]")
                continue

            elif user_input.startswith("o "):
                chat_name = user_input[2:].strip()
                if chat_name:
                    client.load_chat(chat_name)
                else:
                    console.print("[yellow]No chat file specified.[/yellow]")
                continue

            elif user_input.startswith("/model"):
                model_key = user_input[len("/model"):].strip()
                client.switch_model(model_key)
                continue

            client.send_message(user_input)

        except KeyboardInterrupt:
            shutdown()
        except EOFError:
            shutdown()


if __name__ == "__main__":
    main()
