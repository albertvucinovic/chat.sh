import json
from typing import Dict, List, Optional

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class DisplayManager:
    def __init__(self, client: "ChatClient"):
        self.client = client
        self.console = client.console
        self._stream_mode: Optional[str] = None  # "normal" | "tmux" | None
        self._live = None
        self._stream_started = False

    def get_border_style(self, style: str) -> str:
        return style if self.client.borders_enabled else "none"

    def render_system_prompt(self, content: str):
        self.console.print(
            Panel(
                content,
                title="[bold cyan]System Prompt[/bold cyan]",
                border_style=self.get_border_style("dim"),
                box=self.client.boxStyle
            )
        )

    def render_message(self, msg: Dict, is_loading: bool = False) -> None:
        """Renders a single message to the console."""
        try:
            role = msg.get("role")
            if role == "user":
                # Get the model from the message, or fall back to the current client model
                model_name = msg.get("model_key", self.client.current_model_key)
                title = f"[bold green]You & {model_name}[/bold green]"
                border_style = "green"
                content_renderable = Text(msg.get("content", "") or "[No content]", no_wrap=False, overflow="fold")
                self.console.print(Panel(content_renderable, title=title, border_style=border_style, box=self.client.boxStyle), crop=False)

            elif role == "assistant":
                self.console.print(self._create_assistant_panel(msg))

            elif role == "tool":
                output_renderable = Text(msg.get("content", "") or "[No output]", no_wrap=False, overflow="fold")
                self.console.print(Panel(
                    output_renderable,
                    title=f"[bold green]Tool Output: {msg.get('name', 'N/A')}[/bold green]",
                    border_style="green",
                    box=self.client.boxStyle
                ), crop=False)
                # Special pretty summary for wait_agents
                try:
                    if msg.get('name') == 'wait_agents':
                        data = json.loads(msg.get('content') or '{}')
                        results = data.get('results', {}) if isinstance(data, dict) else {}
                        if results:
                            lines: List[str] = []
                            for cid, res in results.items():
                                if not isinstance(res, dict):
                                    res = {}
                                rv = res.get('return_value', '')
                                summ = res.get('summary', '')
                                status = res.get('status', '')
                                line = f"{cid}: {rv}" if rv else f"{cid}: (no return_value)"
                                if summ:
                                    line += f" — {summ}"
                                if status and status != 'done':
                                    line += f" [{status}]"
                                lines.append(line)
                            if lines:
                                self.console.print(Panel(
                                    Text("\n".join(lines), no_wrap=False, overflow="fold"),
                                    title="[bold cyan]Wait Results[/bold cyan]",
                                    border_style=self.get_border_style("cyan"),
                                    box=self.client.boxStyle
                                ), crop=False)
                except Exception:
                    pass
                # Pretty summary for list_agents
                try:
                    if msg.get('name') == 'list_agents':
                        data = json.loads(msg.get('content') or '{}')
                        parents = data.get('parents', {}) if isinstance(data, dict) else {}
                        if parents:
                            lines: List[str] = []
                            for pid, children in parents.items():
                                lines.append(f"{pid}:")
                                for ch in children:
                                    cid = ch.get('child_id', '')
                                    status = ch.get('status', '')
                                    rv = ch.get('return_value', '')
                                    line = f"  - {cid} [{status}]"
                                    if rv:
                                        line += f" — {rv}"
                                    lines.append(line)
                            if lines:
                                self.console.print(Panel(
                                    Text("\n".join(lines), no_wrap=False, overflow="fold"),
                                    title="[bold cyan]Agent Tree[/bold cyan]",
                                    border_style=self.get_border_style("cyan"),
                                    box=self.client.boxStyle
                                ), crop=False)
                except Exception:
                    pass
        except Exception as e:
            self.console.print(f"[red]Error rendering message: {e}[/red]")

    def _create_assistant_panel(self, msg: Dict, live_model_name: Optional[str] = None) -> Panel:
        """Creates a rich Panel for an assistant message, including tool calls."""
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        renderables = []

        # For live streaming, use the provided name. For loaded chats, get it from the message.
        model_name = live_model_name or msg.get("model_key", self.client.current_model_key)
        title = f"[bold cyan]Assistant ({model_name})[/bold cyan]"

        if content:
            renderables.append(Text(content, justify="left", no_wrap=False, overflow="fold"))

        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                name, args_str = func.get("name", "..."), func.get("arguments", "")
                try:
                    script = json.loads(args_str or '{}').get('script', args_str)
                    lang = name if name in ["python", "bash"] else "json"
                    renderables.append(Panel(
                        Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True),
                        title=f"[bold yellow]Tool Call: {name}[/bold yellow]",
                        border_style="yellow",
                        box=self.client.boxStyle
                    ))
                except (json.JSONDecodeError, AttributeError):
                    renderables.append(Panel(
                        Text(args_str, no_wrap=False, overflow="fold"),
                        title=f"[bold yellow]Tool Call: {name}[/bold yellow]",
                        border_style="yellow",
                        box=self.client.boxStyle
                    ))

        if not renderables:
            renderables.append(Text("[No content or tool calls]"))

        return Panel(
            Group(*renderables),
            title=title,
            border_style="cyan",
            box=self.client.boxStyle
        )

    # Streaming API centralization
    def begin_stream(self, model_name: str, mode: str):
        """Start streaming in the selected mode: 'normal' uses Live; 'tmux' uses simple borders."""
        self._stream_mode = mode
        self._stream_started = False
        if mode == "normal":
            # Delay Live creation; create on first update to show placeholder
            from rich.live import Live
            self._live = Live(console=self.console, auto_refresh=False, vertical_overflow="visible")
            self._live.__enter__()
            self._live.update(self.create_live_display(None, {}), refresh=True)
        elif mode == "tmux":
            # Print a fixed top border header once; no reflow later
            header = Panel("Streaming...", title=f"[bold cyan]Assistant ({model_name})[/bold cyan]", border_style="cyan", box=self.client.boxStyle)
            self.console.print(header)
            # Prepare a subtle prefix for stream lines
            self.console.print("", end="")

    def stream_chunk(self, content: Optional[str] = None, reasoning: Optional[str] = None, tool_calls_delta: Optional[Dict] = None, model_name: Optional[str] = None, buffers: Optional[Dict] = None):
        """Feed a streaming delta. For normal mode, re-render Live; for tmux, append-only raw write."""
        if self._stream_mode == "normal":
            # Expect buffers to contain 'assistant_text_parts', 'reasoning_parts', and 'tool_calls_buf'
            if not self._live:
                return
            self._live.update(self.create_live_display(
                "".join(buffers.get("reasoning_parts", [])) if buffers else None,
                {"content": "".join(buffers.get("assistant_text_parts", [])) if buffers else "", "tool_calls": list((buffers.get("tool_calls_buf") or {}).values())}
            ), refresh=True)
        elif self._stream_mode == "tmux":
            if content:
                # Start with a newline once to avoid colliding with prompt
                if not self._stream_started:
                    self.console.print("")
                    self._stream_started = True
                # Append chunk without reflow; use stdout to minimize interference
                try:
                    import sys as _sys
                    _sys.stdout.write(content)
                    _sys.stdout.flush()
                except Exception:
                    # Fallback
                    self.console.print(content)

    def end_stream(self, final_assistant_msg: Dict):
        """Finish streaming. Close Live if used; in tmux, print a closing border or nothing to avoid duplication."""
        mode = self._stream_mode
        self._stream_mode = None
        if mode == "normal":
            if self._live:
                # Final re-render with completed content
                self._live.update(self._create_assistant_panel(final_assistant_msg, live_model_name=self.client.current_model_key), refresh=True)
                self._live.__exit__(None, None, None)
                self._live = None
        elif mode == "tmux":
            # Do NOT re-render panel to avoid duplication; optionally print a thin rule
            try:
                self.console.rule(style=self.get_border_style("cyan"))
            except Exception:
                pass

    def create_live_display(self, reasoning: Optional[str], assistant_msg: Dict) -> Group:
        """Creates the renderable Group for the Live display during streaming."""
        renderables = []
        
        # 1. Add the Reasoning panel only if it has content and the setting is enabled.
        if self.client.show_thinking and reasoning:
            renderables.append(Panel(
                Text(reasoning, justify="left", no_wrap=False, overflow="fold"),
                title="[bold magenta]Reasoning[/bold magenta]",
                border_style="magenta",
                box=self.client.boxStyle
            ))

        # 2. Determine if the main assistant response has any content yet.
        has_assistant_content = assistant_msg.get("content") or assistant_msg.get("tool_calls")

        # 3. If there's no reasoning and no assistant content, show a single placeholder.
        #    Otherwise, show the normal assistant panel.
        if not renderables and not has_assistant_content:
            # The entire display is empty, so show a placeholder.
            renderables.append(Panel(
                "[dim]Assistant is thinking...[/dim]",
                border_style="cyan",
                box=self.client.boxStyle
            ))
        else:
            # There's either reasoning or assistant content, so show the assistant panel.
            # Pass the current model name for live display.
            renderables.append(self._create_assistant_panel(assistant_msg, live_model_name=self.client.current_model_key))
        
        return Group(*renderables)
