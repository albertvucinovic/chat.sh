import os
import re
import json
import sys
import time
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory

from chat_client import ChatClient
from completer import PtkCompleter
from executors import run_bash_script


def ensure_tree_id(console: Console):
    """Ensure this process has a fresh tree id unless EG_TREE_ID is preset."""
    if os.environ.get('EG_TREE_ID'):
        return os.environ['EG_TREE_ID']
    tree_id = str(int(time.time()))
    base = Path('.egg/agents')
    base.mkdir(parents=True, exist_ok=True)
    (base / '.current_tree').write_text(tree_id)
    os.environ['EG_TREE_ID'] = tree_id
    console.print(Panel(f"Started new agent tree: {tree_id}", title="[bold]Agent Tree[/bold]", border_style="magenta"))
    return tree_id


def _record_tmux_pane_if_available(console: Console):
    """Record TMUX_PANE into this agent's state.json if running as an agent inside tmux."""
    agent_dir = os.environ.get('EG_AGENT_DIR')
    pane = os.environ.get('TMUX_PANE', '')
    if not agent_dir or not pane:
        return
    try:
        state_path = Path(agent_dir) / 'state.json'
        if state_path.exists():
            try:
                with open(state_path, 'r') as f:
                    st = json.load(f)
            except Exception:
                st = {}
        else:
            st = {}
        if st.get('pane_id') != pane:
            st['pane_id'] = pane
            with open(state_path, 'w') as f:
                json.dump(st, f, indent=2)
    except Exception as e:
        console.print(f"[yellow]Warning: could not record TMUX_PANE: {e}[/yellow]")


def main():
    console = Console()

    # Ensure per-run new tree unless explicitly provided
    ensure_tree_id(console)

    try:
        client = ChatClient()
    except ValueError as e:
        console.print(f"[bold red]Error: {e}[/bold red]")
        console.print("Please provide necessary API environment variables.")
        return

    # Record current pane id for deterministic pane targeting
    _record_tmux_pane_if_available(console)

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
            "[bold]/popContext <return_value>[/bold] - Pop context from stack and return to previous. For subagents: finalize and return result to parent.\n"
            "[bold]/spawn <file.md?> <text>[/bold] - Spawn child like pushContext.\n"
            "[bold]/spawn_auto <file.md?> <text>[/bold] - Spawn child with auto tool-approval.\n"
            "[bold]/wait <child_id|space-separated list>|any|all[/bold] - Wait for specific child agents, any, or all.\n"
            "[bold]/tree[/bold] - List children in current tree.  [bold]/attach <tree_id?> [agent_id?][/bold] - Attach tmux.\n"
            "[bold]/tree use <tree_id>[/bold] - Switch active agent tree for this session.  [bold]/tree list[/bold] - List existing trees.\n"
            "[bold]/o <tree_id>|list[/bold] - Attach to a tree's tmux session (list to show trees).\n",
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
                instruction = "[SYSTEM NOTE] You are a subagent. When you finish this task, you MUST call the /popContext command with your result. If the result is longer, you can create a file to store it. Use the popContext tool. Example: /popContext My result is in ./output.md"
                # Ensure the model sees the instruction inline in the prompt
                client.messages.append({"role": "user", "content": f"{init_text}\n\n{instruction}"})
                # Display subagent info and initial context visibly
                tree_id = os.environ.get('EG_TREE_ID')
                parent_id = os.environ.get('EG_PARENT_ID')
                agent_id = os.environ.get('EG_AGENT_ID')
                console.print(Panel(
                    f"Subagent active.\nTree: {tree_id}\nParent: {parent_id}\nAgent: {agent_id}\n\nWhen finished, run: /popContext <return_value>",
                    title="[bold]Subagent Context[/bold]",
                    border_style=client.get_border_style("magenta"),
                    box=client.boxStyle
                ))
                console.print(Panel(Text(init_text), title="[bold]Initial Context[/bold]", border_style=client.get_border_style("cyan"), box=client.boxStyle))
                console.print(Panel(Text(instruction), title="[bold]How to Finish[/bold]", border_style=client.get_border_style("yellow"), box=client.boxStyle))
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
                # Always handle /spawn locally. Do not fallback to model tool-call on errors.
                client.messages.append({"role": "user", "content": user_input})
                match = re.match(r"/spawn\s*(\S+\.md)?\s*(.*)", user_input)
                if not match:
                    console.print("[yellow]Usage: /spawn [<file_path.md>] [<additional_text>] [/yellow]")
                    continue
                file_path = match.group(1)
                additional_text = match.group(2).strip()
                context_parts = []
                label = None
                # Validate file_path actually exists; otherwise treat everything as additional_text
                resolved_fp = None
                if file_path:
                    try:
                        if file_path.startswith("global/"):
                            script_dir = os.path.dirname(os.path.realpath(__file__))
                            resolved_fp = os.path.join(script_dir, 'global_commands', file_path[len('global/'):])
                        else:
                            resolved_fp = file_path
                        if not (resolved_fp and os.path.isfile(resolved_fp)):
                            additional_text = (file_path + ' ' + additional_text).strip()
                            file_path = None
                            resolved_fp = None
                    except Exception:
                        additional_text = (file_path + ' ' + additional_text).strip()
                        file_path = None
                        resolved_fp = None
                if resolved_fp:
                    try:
                        with open(resolved_fp, 'r', encoding='utf-8') as f:
                            context_parts.append(f.read())
                        label = os.path.splitext(os.path.basename(file_path))[0]
                    except Exception as e:
                        console.print(f"[red]Error reading file: {e}[/red]")
                        continue
                if additional_text:
                    context_parts.append(additional_text)
                # Append finishing instruction after content
                finishing_instruction = "[SYSTEM NOTE] You are a subagent. When you finish your task, you MUST call the /popContext command with a concise return value (e.g., a path to your output or a short summary). Example: /popContext ./output.md"
                context_parts.append(finishing_instruction)
                context_text = "\n\n".join(context_parts).strip()
                if not context_text:
                    console.print("[yellow]Usage: /spawn [<file_path.md>] [<additional_text>] [/yellow]")
                    continue
                if not label:
                    label = (additional_text.split()[:1] or ["child"])[0]

                # Always use local tool spawn; on error, report and continue without involving model
                try:
                    from tool_manager import tool_spawn_agent
                    result_json = tool_spawn_agent({"context_text": context_text, "label": label})
                    console.print(Panel(Text(result_json), title="[bold green]Spawned Agent[/bold green]", border_style="green", box=client.boxStyle))
                    client.messages.append({"role": "tool", "name": "spawn_agent", "tool_call_id": f"local_{label}", "content": result_json})
                except Exception as e:
                    console.print(Panel(f"Spawn failed: {e}", title="[bold red]Spawn Error[/bold red]", border_style="red", box=client.boxStyle))
                continue

            elif user_input.startswith("/spawn_auto"):
                # Always handle /spawn_auto locally. Do not fallback to model tool-call on errors.
                client.messages.append({"role": "user", "content": user_input})
                match = re.match(r"/spawn_auto\s*(\S+\.md)?\s*(.*)", user_input)
                if not match:
                    console.print("[yellow]Usage: /spawn_auto [<file_path.md>] [<additional_text>] [/yellow]")
                    continue
                file_path = match.group(1)
                additional_text = match.group(2).strip()
                context_parts = []
                label = None
                # Validate file_path actually exists; otherwise treat everything as additional_text
                resolved_fp = None
                if file_path:
                    try:
                        if file_path.startswith("global/"):
                            script_dir = os.path.dirname(os.path.realpath(__file__))
                            resolved_fp = os.path.join(script_dir, 'global_commands', file_path[len('global/'):])
                        else:
                            resolved_fp = file_path
                        if not (resolved_fp and os.path.isfile(resolved_fp)):
                            additional_text = (file_path + ' ' + additional_text).strip()
                            file_path = None
                            resolved_fp = None
                    except Exception:
                        additional_text = (file_path + ' ' + additional_text).strip()
                        file_path = None
                        resolved_fp = None
                if resolved_fp:
                    try:
                        with open(resolved_fp, 'r', encoding='utf-8') as f:
                            context_parts.append(f.read())
                        label = os.path.splitext(os.path.basename(file_path))[0]
                    except Exception as e:
                        console.print(f"[red]Error reading file: {e}[/red]")
                        continue
                if additional_text:
                    context_parts.append(additional_text)
                # Append finishing instruction after content
                finishing_instruction = "[SYSTEM NOTE] You are a subagent. When you finish your task, you MUST call the /popContext command with a concise return value (e.g., a path to your output or a short summary). Example: /popContext ./output.md"
                context_parts.append(finishing_instruction)
                context_text = "\n\n".join(context_parts).strip()
                if not context_text:
                    console.print("[yellow]Usage: /spawn_auto [<file_path.md>] [<additional_text>] [/yellow]")
                    continue
                if not label:
                    label = (additional_text.split()[:1] or ["child"])[0]

                # Always use local tool spawn; on error, report and continue without involving model
                try:
                    from tool_manager import tool_spawn_agent_auto
                    result_json = tool_spawn_agent_auto({"context_text": context_text, "label": label})
                    console.print(Panel(Text(result_json), title="[bold green]Spawned Agent (auto)[/bold green]", border_style="green", box=client.boxStyle))
                    client.messages.append({"role": "tool", "name": "spawn_agent_auto", "tool_call_id": f"local_{label}_auto", "content": result_json})
                except Exception as e:
                    console.print(Panel(f"Spawn failed: {e}", title="[bold red]Spawn Error[/bold red]", border_style="red", box=client.boxStyle))
                continue

            elif user_input.startswith("/wait"):
                rest = user_input[len("/wait"):].strip()
                client.messages.append({"role": "user", "content": user_input})
                # Interpret keywords any/all
                parts = rest.split()
                args_obj = {}
                if len(parts) == 0:
                    console.print("[yellow]Usage: /wait <child_id> [child_id2 ...] | any | all\nUse child IDs exactly as shown by /tree (e.g., label-001).[/yellow]")
                    continue
                if len(parts) == 1 and parts[0].lower() in ("any", "all"):
                    mode = parts[0].lower()
                    if mode == "all":
                        args_obj = {"which": []}  # interpreted as all current children
                    else:
                        args_obj = {"which": [], "any_mode": True}
                else:
                    args_obj = {"which": parts}
                # Try local execution first
                try:
                    from tool_manager import tool_wait_agents
                    result_json = tool_wait_agents(args_obj)
                    console.print(Panel(Text(result_json), title="[bold green]Wait Agents[/bold green]", border_style="green", box=client.boxStyle))
                    client.messages.append({"role": "tool", "name": "wait_agents", "tool_call_id": f"local_wait", "content": result_json})
                except KeyboardInterrupt:
                    # Gracefully handle Ctrl+C to cancel wait without exiting the app
                    result_json = json.dumps({"interrupted": True, "message": "wait interrupted by user"}, indent=2)
                    console.print(Panel(Text(result_json), title="[bold yellow]Wait Interrupted[/bold yellow]", border_style="yellow", box=client.boxStyle))
                    client.messages.append({"role": "tool", "name": "wait_agents", "tool_call_id": f"local_wait", "content": result_json})
                except Exception:
                    # Fallback to model tool call
                    args = json.dumps(args_obj)
                    tool_call_json = json.dumps({
                        "tool_calls": [
                            {"type": "function", "function": {"name": "wait_agents", "arguments": args}}
                        ]
                    })
                    client.send_message(tool_call_json)
                continue

            elif user_input.startswith("/o"):
                parts = user_input.split()
                base = Path('.egg/agents')
                if len(parts) == 1 or (len(parts) == 2 and parts[1] == 'list'):
                    trees = [d.name for d in base.iterdir() if d.is_dir() and d.name != '.current_tree'] if base.exists() else []
                    current = os.environ.get('EG_TREE_ID', (base / '.current_tree').read_text().strip() if (base / '.current_tree').exists() else '')
                    lines = []
                    for t in sorted(trees):
                        if t == current:
                            lines.append(f"* {t} (current)")
                        else:
                            lines.append(f"  {t}")
                    tree_list = "\n".join(lines) or "<no trees>"
                    console.print(Panel(Text(tree_list), title="[bold cyan]Trees (/o list)[/bold cyan]", border_style="cyan", box=client.boxStyle))
                    continue
                elif len(parts) >= 2:
                    tree_id = parts[1]
                    # Try to attach existing tmux session for the tree
                    script = f"script/agents/attach_agent.sh {tree_id}"
                    output = run_bash_script(script)
                    if 'no server running' in output.lower() or 'no sessions' in output.lower():
                        console.print(Panel("Session not found. Tree reconstruction is not yet implemented in this step.", title="[bold yellow]/o attach[/bold yellow]", border_style="yellow", box=client.boxStyle))
                    else:
                        console.print(Panel(Text(output), title="[bold cyan]tmux attach[/bold cyan]", border_style="cyan", box=client.boxStyle))
                    continue

            elif user_input.startswith("/tree "):
                parts = user_input.split()
                if len(parts) >= 2 and parts[1] == 'list':
                    base = Path('.egg/agents')
                    trees = [d.name for d in base.iterdir() if d.is_dir() and d.name != '.current_tree'] if base.exists() else []
                    current = os.environ.get('EG_TREE_ID', (base / '.current_tree').read_text().strip() if (base / '.current_tree').exists() else '')
                    lines = []
                    for t in sorted(trees):
                        if t == current:
                            lines.append(f"* {t} (current)")
                        else:
                            lines.append(f"  {t}")
                    tree_list = "\n".join(lines) or "<no trees>"
                    console.print(Panel(Text(tree_list), title="[bold cyan]Agent Trees[/bold cyan]", border_style="cyan", box=client.boxStyle))
                    continue
                if len(parts) >= 3 and parts[1] == 'use':
                    new_id = parts[2]
                    base = Path('.egg/agents')
                    if not (base / new_id).exists():
                        console.print(f"[red]Tree '{new_id}' does not exist.[/red]")
                        continue
                    (base / '.current_tree').write_text(new_id)
                    os.environ['EG_TREE_ID'] = new_id
                    console.print(Panel(f"Switched to tree: {new_id}", title="[bold]Agent Tree[/bold]", border_style="magenta", box=client.boxStyle))
                    continue

            elif user_input.startswith("/tree"):
                # Show all children across the tree using tool
                try:
                    from tool_manager import tool_list_agents
                    result_json = tool_list_agents({"tree_id": os.environ.get('EG_TREE_ID')})
                    console.print(Panel(Text(result_json), title="[bold cyan]Agent Tree[/bold cyan]", border_style="cyan", box=client.boxStyle))
                    try:
                        data = json.loads(result_json)
                        parents = data.get("parents", {})
                        lines = []
                        for pid, children in parents.items():
                            lines.append(f"{pid}:")
                            for ch in children:
                                cid = ch.get("child_id", "")
                                status = ch.get("status", "")
                                rv = ch.get("return_value", "")
                                line = f"  - {cid} [{status}]"
                                if rv:
                                    line += f" — {rv}"
                                lines.append(line)
                        pretty = "\n".join(lines) or "<no children>"
                        console.print(Panel(Text(pretty), title="[bold cyan]Agent Tree (Pretty)[/bold cyan]", border_style="cyan", box=client.boxStyle))
                    except Exception:
                        pass
                except Exception as e:
                    console.print(f"[red]Error listing agents: {e}[/red]")
                continue

            elif user_input.startswith("/attach"):
                parts = user_input.split()
                if len(parts) < 2:
                    base = Path('.egg/agents')
                    tree_id = os.environ.get('EG_TREE_ID') or ((base / '.current_tree').read_text().strip() if (base / '.current_tree').exists() else '')
                    if not tree_id:
                        console.print("[yellow]Usage: /attach <tree_id> [agent_id][/yellow]")
                        continue
                    agent_id = ''
                else:
                    tree_id = parts[1]
                    agent_id = parts[2] if len(parts) > 2 else ''
                script = f"script/agents/attach_agent.sh {tree_id} {agent_id}"
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
