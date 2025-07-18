import sys
import os
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

    def get_continuation_message(width, line_number, wrap_count):
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

    @kb.add('right')
    def _(event):
        """
        Accepts the current completion.
        - If completion menu is visible, accepts the selected completion.
        - Otherwise, accepts the auto-suggestion (gray text).
        """
        if event.current_buffer.complete_state:
            completion = event.current_buffer.complete_state.current_completion
            if completion:
                event.current_buffer.apply_completion(completion)
        else:
            suggestion = event.current_buffer.suggestion
            if suggestion:
                event.current_buffer.insert_text(suggestion.text)

    @kb.add("c-b")
    def _(event):
        """Toggles UI borders and prints a status message above the prompt."""
        client.toggle_borders()

    session.key_bindings = kb

    console.print(
        Panel(
            "Chat started. [bold]Tab[/bold] to autocomplete, [bold]Right Arrow[/bold] to accept.\n"
            "[bold]Ctrl+D[/bold] to submit. [bold]Ctrl+B[/bold] for borders. [bold]Ctrl+E[/bold] to clear. [bold]Ctrl+C[/bold] to exit.\n"
            "[bold]/pushContext <context>[/bold] - Push current chat and start new context.\n"
            "[bold]/popContext <return_value>[/bold] - Pop context from stack and return to previous.\n",
            title="[bold]Welcome[/bold]",
            border_style=client.get_border_style("magenta")
        )
    )

    def shutdown():
        """
        Saves the chat and exits cleanly.
        Note: Context stack management is handled within ChatClient methods
        (push_context/pop_context) which save specific sub-contexts.
        This save_chat is for the root level or final state.
        """
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
                client.send_message(user_input) #Send empty messages also
                continue

            elif user_input.startswith("b "):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                console.print("\n[cyan]Executing local command...[/cyan]")
                script_to_run = user_input[2:].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    output_renderable = Text(output)
                    console.print(Panel(
                        output_renderable, title="[bold green]Local Command Output[/bold green]", border_style="green", box = client.boxStyle))
                    context_message = (
                        "User executed a local command.\n"
                        f"Command:\n\`\`\`bash\n{script_to_run}\n\`\`\`\n\n"
                        f"Output:\n---\n{output}\n---"
                    )
                    client.send_context_only(context_message)
                else:
                    console.print(
                        "[yellow]Empty bash command, skipping.[/yellow]")
                continue

            elif user_input.startswith("o "):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                chat_name = user_input[2:].strip()
                if chat_name:
                    client.load_chat(chat_name)
                else:
                    console.print("[yellow]No chat file specified.[/yellow]")
                continue

            elif user_input.startswith("/global/"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                command_file = user_input[len("/global/"):].strip()
                if not command_file.endswith(".md"):
                    command_file += ".md"

                script_dir = os.path.dirname(os.path.realpath(__file__))
                file_path = os.path.join(
                    script_dir, "global_commands", command_file)

                try:
                    with open(file_path, 'r') as f:
                        command_content = f.read()
                    console.print(Panel(
                        f"Executing global command: [bold]{command_file}[/bold]", border_style="yellow", box = client.boxStyle))
                    client.send_message(command_content)
                except FileNotFoundError:
                    console.print(
                        f"[bold red]Error: Global command file not found: {command_file}[/bold red]")
                except Exception as e:
                    console.print(
                        f"[bold red]Error executing global command: {e}[/bold red]")
                continue

            elif user_input.startswith("/model"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                model_key = user_input[len("/model"):].strip()
                client.switch_model(model_key)
                continue

            elif user_input.startswith("/pushContext"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                context = user_input[len("/pushContext"):].strip()
                if context:
                    result = client.push_context(context)
                    console.print(Panel(
                        result, title="[bold cyan]Context Management[/bold cyan]", border_style="cyan", box = client.boxStyle))
                else:
                    console.print("[yellow]Usage: /pushContext <new_context>[/yellow]")
                continue

            elif user_input.startswith("/popContext"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                return_value = user_input[len("/popContext"):].strip()
                if return_value:
                    result = client.pop_context(return_value)
                    console.print(Panel(
                        result, title="[bold cyan]Context Management[/bold cyan]", border_style="cyan", box = client.boxStyle))
                else:
                    console.print("[yellow]Usage: /popContext <return_value>[/yellow]")
                continue

            elif user_input.startswith("/toggleYesToolFlag"):
                client.yesTooolFlag = not client.yesTooolFlag
                if client.yesTooolFlag:
                    print("TOOL CALLS WILL AUTOMATICALLY GO THROUGH")
                else:
                    print("Tool calls need confirmation")
                continue

            client.send_message(user_input)

        except KeyboardInterrupt:
            shutdown()
        except EOFError:
            shutdown()


if __name__ == "__main__":
    main()
