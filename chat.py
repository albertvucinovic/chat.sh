import os
import re
import json
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
    console = Console()
    try:
        client = ChatClient()
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        console.print("Please provide necessary API environment variables.")
        return

    def get_prompt_message():
        model_name = client.current_model_key
        return f"[You & {model_name}]: " if client.borders_enabled else f"You & {model_name}: "

    def get_continuation_message(width, line_number, wrap_count):
        return "[...] " if client.borders_enabled else "... "

    session = PromptSession(
        message=get_prompt_message,
        completer=PtkCompleter(client),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=True,
        prompt_continuation=get_continuation_message,
    )

    kb = KeyBindings()

    @kb.add("c-d")
    def _(event):
        event.app.exit(result=event.current_buffer.text)

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-e")
    def _(event):
        event.current_buffer.reset()

    @kb.add('right')
    def _(event):
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
        client.toggle_borders()

    session.key_bindings = kb

    console.print(
        Panel(
            "Chat started. [bold]Tab[/bold] to autocomplete, [bold]Right Arrow[/bold] to accept.\n"
            "[bold]Ctrl+D[/bold] to submit. [bold]Ctrl+B[/bold] for borders. [bold]Ctrl+E[/bold] to clear. [bold]Ctrl+C[/bold] to exit.\n"
            "[bold]/pushContext <context_or_file.md>[/bold] - Push current chat and start new context.\n"
            "[bold]/popContext <return_value>[/bold] - Pop context from stack and return to previous.\n"
            "[bold]/spawn <file.md?> <text>[/bold] - Spawn child like pushContext.\n"
            "[bold]/wait <child_id|all|any or space-separated list>[/bold] - Wait for child agents.\n"
            "[bold]/tree[/bold] - List children of current agent.  [bold]/attach <tree_id?> [agent_id?][/bold] - Attach tmux.\n",
            title="[bold]Welcome[/bold]",
            border_style=client.get_border_style("magenta")
        )
    )

    def shutdown():
        console.print("\n\n[bold yellow]Saving chat and exiting...[/bold yellow]")
        saved_path = client.save_chat()
        console.print(f"[green]Chat saved to:[/green] {saved_path}")
        sys.exit(0)

    # Auto-inject initial context for child agents
    try:
        agent_dir = os.environ.get('EG_AGENT_DIR')
        init_ctx_file = os.environ.get('EG_INIT_CONTEXT_FILE')
        consumed_marker = os.path.join(agent_dir, '.context_consumed') if agent_dir else None
        if init_ctx_file and os.path.isfile(init_ctx_file) and (not consumed_marker or not os.path.exists(consumed_marker)):
            with open(init_ctx_file, 'r', encoding='utf-8') as f:
                init_text = f.read().strip()
            if init_text:
                instruction = "[SYSTEM NOTE: When you finish, call popContext with a concise return value (e.g., a path to your output or a short summary).]"
                client.messages.append({"role": "user", "content": f"{instruction}\n\n{init_text}"})
                client.send_message("")
                if consumed_marker:
                    with open(consumed_marker, 'w') as cf:
                        cf.write('1')
    except Exception:
        pass

    while True:
        try:
            client.in_single_turn_auto_execute_calls = False
            user_input = session.prompt().strip()

            if not user_input:
                client.send_message(user_input)
                continue

            elif user_input.startswith("b "):
                client.messages.append({"role": "user", "content": user_input})
                console.print("\n[cyan]Executing local command...[/cyan]")
                script_to_run = user_input[2:].strip()
                if script_to_run:
                    output = run_bash_script(script_to_run)
                    output_renderable = Text(output)
                    console.print(Panel(output_renderable, title="[bold green]Local Command Output[/bold green]", border_style="green", box=client.boxStyle))
                    context_message = (
                        "User executed a local command.\n"
                        f"Command:\n```bash\n{script_to_run}\n```\n\n"
                        f"Output:\n---\n{output}\n---"
                    )
                    client.send_context_only(context_message)
                else:
                    console.print("[yellow]Empty bash command, skipping.[/yellow]")
                continue

            elif user_input.startswith("/model"):
                client.messages.append({"role": "user", "content": user_input})
                model_key = user_input[len("/model"):].strip()
                client.switch_model(model_key)
                continue

            elif user_input.startswith("/pushContext"):
                client.messages.append({"role": "user", "content": user_input})
                match = re.match(r"/pushContext\s*(\S+\.md)?\s*(.*)", user_input)
                if match:
                    file_path = match.group(1)
                    additional_text = match.group(2).strip()
                    if file_path or additional_text:
                        result = client.push_context(file_path, additional_text)
                        console.print(Panel(result, title="[bold cyan]Context Management[/bold cyan]", border_style="cyan", box=client.boxStyle))
                        if not result.startswith("Error:"):
                            client.send_message("")
                    else:
                        console.print("[yellow]Usage: /pushContext [<file_path.md>] [<additional_text>][/yellow]")
                else:
                    console.print("[yellow]Usage: /pushContext [<file_path.md>] [<additional_text>][/yellow]")
                continue

            elif user_input.startswith("/popContext"):
                client.messages.append({"role": "user", "content": user_input})
                return_value = user_input[len("/popContext"):].strip()
                if return_value:
                    result = client.pop_context(return_value)
                    console.print(Panel(result, title="[bold cyan]Context Management[/bold cyan]", border_style="cyan", box=client.boxStyle))
                else:
                    console.print("[yellow]Usage: /popContext <return_value>[/yellow]")
                continue

            elif user_input.startswith("/toggleYesToolFlag"):
                client.yesToolFlag = not client.yesToolFlag
                print("TOOL CALLS WILL AUTOMATICALLY GO THROUGH" if client.yesToolFlag else "Tool calls need confirmation")
                continue

            elif user_input.startswith("/toggleThinkingDisplay"):
                client.toggle_thinking_display()
                continue

            elif user_input.startswith("/spawn"):
                client.messages.append({"role": "user", "content": user_input})
                match = re.match(r"/spawn\s*(\S+\.md)?\s*(.*)", user_input)
                if not match:
                    console.print("[yellow]Usage: /spawn [<file_path.md>] [<additional_text>] [/yellow]")
                    continue
                file_path = match.group(1)
                additional_text = match.group(2).strip()
                context_parts = []
                label = None
                if file_path:
                    try:
                        if file_path.startswith("global/"):
                            script_dir = os.path.dirname(os.path.realpath(__file__))
                            fp = os.path.join(script_dir, 'global_commands', file_path[len('global/'):])
                        else:
                            fp = file_path
                        with open(fp, 'r', encoding='utf-8') as f:
                            context_parts.append(f.read())
                        label = os.path.splitext(os.path.basename(file_path))[0]
                    except Exception as e:
                        console.print(f"[red]Error reading file: {e}[/red]")
                if additional_text:
                    context_parts.append(additional_text)
                context_text = "\n\n".join(context_parts).strip()
                if not label:
                    label = (additional_text.split()[:1] or ["child"])[0]

                try:
                    from tool_manager import tool_spawn_agent
                    result_json = tool_spawn_agent({"context_text": context_text, "label": label})
                    console.print(Panel(Text(result_json), title="[bold green]Spawned Agent[/bold green]", border_style="green", box=client.boxStyle))
                    client.messages.append({"role": "tool", "name": "spawn_agent", "tool_call_id": f"local_{label}", "content": result_json})
                except Exception:
                    tool_call_json = json.dumps({
                        "tool_calls": [
                            {"type": "function", "function": {"name": "spawn_agent", "arguments": json.dumps({"context_text": context_text, "label": label})}}
                        ]
                    })
                    client.send_message(tool_call_json)
                continue

            elif user_input.startswith("/wait"):
                rest = user_input[len("/wait"):].strip()
                client.messages.append({"role": "user", "content": user_input})
                if not rest:
                    args = json.dumps({"which": "all"})
                else:
                    parts = rest.split()
                    if len(parts) == 1:
                        args = json.dumps({"which": parts[0]})
                    else:
                        args = json.dumps({"which": parts})
                tool_call_json = json.dumps({
                    "tool_calls": [
                        {"type": "function", "function": {"name": "wait_agents", "arguments": args}}
                    ]
                })
                client.send_message(tool_call_json)
                continue

            elif user_input.startswith("/tree"):
                tree_id = os.environ.get('EG_TREE_ID') or (open('.egg/agents/.current_tree').read().strip() if os.path.exists('.egg/agents/.current_tree') else 'default')
                parent_id = os.environ.get('EG_AGENT_ID', 'root')
                children_dir = os.path.join('.egg/agents', tree_id, parent_id, 'children')
                if not os.path.isdir(children_dir):
                    console.print("[yellow]No children found for this agent.[/yellow]")
                    continue
                output = run_bash_script(f".egg/agents/bin/list_agents.sh {tree_id}")
                console.print(Panel(Text(output), title="[bold cyan]Agent Tree[/bold cyan]", border_style="cyan", box=client.boxStyle))
                continue

            elif user_input.startswith("/attach"):
                parts = user_input.split()
                if len(parts) < 2:
                    tree_id = os.environ.get('EG_TREE_ID') or (open('.egg/agents/.current_tree').read().strip() if os.path.exists('.egg/agents/.current_tree') else '')
                    if not tree_id:
                        console.print("[yellow]Usage: /attach <tree_id> [agent_id][/yellow]")
                        continue
                    agent_id = ''
                else:
                    tree_id = parts[1]
                    agent_id = parts[2] if len(parts) > 2 else ''
                script = f".egg/agents/bin/attach_agent.sh {tree_id} {agent_id}"
                output = run_bash_script(script)
                console.print(Panel(Text(output), title="[bold cyan]tmux attach[/bold cyan]", border_style="cyan", box=client.boxStyle))
                continue

            client.send_message(user_input)

        except KeyboardInterrupt:
            console.print("[bold yellow]\nInterrupted.[/bold yellow]")
            shutdown()
        except EOFError:
            shutdown()


if __name__ == "__main__":
    main()
