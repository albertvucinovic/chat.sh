import sys
import os
import re # Added for pushContext parsing
import json
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
        model_name = client.current_model_key
        return f"[You & {model_name}]: " if client.borders_enabled else f"You & {model_name}: "

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
            "[bold]/pushContext <context_or_file.md>[/bold] - Push current chat and start new context.\n"
            "[bold]/popContext <return_value>[/bold] - Pop context from stack and return to previous.\n"
            "[bold]/spawn <file.md?> <text> [--tree X --parent Y --label Z --count N][/bold] - Spawn child like pushContext.\n"
            "[bold]/wait {json}[/bold] - Wait for child agents (all/any/ids).\n"
            "[bold]/tree <tree_id>[/bold] - List agent tree.  [bold]/attach <tree_id> [agent_id][/bold] - Attach tmux.\n",
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

    def parse_spawn_flags(text: str):
        """Parse optional flags from additional_text: --tree, --parent, --label, --count."""
        flags = {"tree_id": "default", "parent_id": "root", "label": None, "count": 1}
        pattern = r"--(tree|parent|label|count)\s+([^\s]+)"
        for m in re.finditer(pattern, text):
            key, val = m.group(1), m.group(2)
            if key == 'tree': flags['tree_id'] = val
            elif key == 'parent': flags['parent_id'] = val
            elif key == 'label': flags['label'] = val
            elif key == 'count':
                try: flags['count'] = int(val)
                except ValueError: pass
        # strip flags from context_text
        cleaned = re.sub(pattern, '', text).strip()
        return flags, cleaned

    # --- Main Application Loop ---
    while True:
        try:
            client.in_single_turn_auto_execute_calls = False
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
                        f"Command:\n```bash\n{script_to_run}\n```\n\n"
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

            elif user_input.startswith("/model"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                model_key = user_input[len("/model"):].strip()
                client.switch_model(model_key)
                continue

            elif user_input.startswith("/pushContext"):
                client.messages.append({"role": "user", "content": user_input}) # Add command to history
                
                # Regex to parse /pushContext. It captures an optional markdown file path and then the remaining text.
                match = re.match(r"/pushContext\s*(\S+\.md)?\s*(.*)", user_input)
                
                if match:
                    file_path = match.group(1) # Optional .md file path
                    additional_text = match.group(2).strip() # Remaining text
                    
                    if file_path or additional_text:
                        result = client.push_context(file_path, additional_text)
                        console.print(Panel(
                            result, title="[bold cyan]Context Management[/bold cyan]", border_style="cyan", box = client.boxStyle))
                        # If the context push was successful, trigger the assistant's turn.
                        if not result.startswith("Error:"):
                            client.send_message("") # An empty message makes the assistant respond to the new context.
                    else:
                        console.print("[yellow]Usage: /pushContext [<file_path.md>] [<additional_text>][/yellow]")
                else:
                    console.print("[yellow]Usage: /pushContext [<file_path.md>] [<additional_text>][/yellow]")
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
                client.yesToolFlag = not client.yesToolFlag
                if client.yesToolFlag:
                    print("TOOL CALLS WILL AUTOMATICALLY GO THROUGH")
                else:
                    print("Tool calls need confirmation")
                continue
            
            elif user_input.startswith("/toggleThinkingDisplay"):
                client.toggle_thinking_display()
                continue

            elif user_input.startswith("/spawn"):
                client.messages.append({"role": "user", "content": user_input})
                # Parse like /pushContext
                match = re.match(r"/spawn\s*(\S+\.md)?\s*(.*)", user_input)
                if not match:
                    console.print("[yellow]Usage: /spawn [<file_path.md>] [<additional_text with flags>] [/yellow]")
                    continue
                file_path = match.group(1)
                rest = match.group(2).strip()
                flags, cleaned_text = parse_spawn_flags(rest)
                # Determine label
                label = flags['label']
                if not label:
                    if file_path:
                        label = os.path.splitext(os.path.basename(file_path))[0]
                    else:
                        label = (cleaned_text.split()[:1] or ["child"])[0]
                # Build context_text: file content + cleaned_text
                context_parts = []
                if file_path:
                    try:
                        if file_path.startswith("global/"):
                            script_dir = os.path.dirname(os.path.realpath(__file__))
                            fp = os.path.join(script_dir, 'global_commands', file_path[len('global/'):])
                        else:
                            fp = file_path
                        with open(fp, 'r', encoding='utf-8') as f:
                            context_parts.append(f.read())
                    except Exception as e:
                        console.print(f"[red]Error reading file: {e}[/red]")
                if cleaned_text:
                    context_parts.append(cleaned_text)
                context_text = "\n\n".join(context_parts).strip()
                payload = {
                    "tree_id": flags['tree_id'],
                    "parent_id": flags['parent_id'],
                    "specs": [{"label": label, "context_text": context_text, "count": flags['count']}],
                    "max_active": flags['count'] if flags['count'] else 1
                }
                tool_call_json = json.dumps({
                    "tool_calls": [
                        {"type": "function", "function": {"name": "spawn_agents", "arguments": json.dumps(payload)}}
                    ]
                })
                client.send_message(tool_call_json)
                continue

            elif user_input.startswith("/wait "):
                payload = user_input[len("/wait "):].strip()
                client.messages.append({"role": "user", "content": user_input})
                tool_call_json = json.dumps({
                    "tool_calls": [
                        {"type": "function", "function": {"name": "wait_agents", "arguments": payload or "{}"}}
                    ]
                })
                client.send_message(tool_call_json)
                continue

            elif user_input.startswith("/tree"):
                tree_id = user_input.split(maxsplit=1)[1] if len(user_input.split()) > 1 else 'default'
                script = f".egg/agents/bin/list_agents.sh {tree_id}"
                output = run_bash_script(script)
                console.print(Panel(Text(output), title="[bold cyan]Agent Tree[/bold cyan]", border_style="cyan", box=client.boxStyle))
                continue

            elif user_input.startswith("/attach"):
                parts = user_input.split()
                if len(parts) < 2:
                    console.print("[yellow]Usage: /attach <tree_id> [agent_id][/yellow]")
                    continue
                tree_id = parts[1]
                agent_id = parts[2] if len(parts) > 2 else ''
                script = f".egg/agents/bin/attach_agent.sh {tree_id} {agent_id}"
                output = run_bash_script(script)
                console.print(Panel(Text(output), title="[bold cyan]tmux attach[/bold cyan]", border_style="cyan", box=client.boxStyle))
                continue

            client.send_message(user_input)

        except KeyboardInterrupt:
            shutdown()
        except EOFError:
            shutdown()


if __name__ == "__main__":
    main()
