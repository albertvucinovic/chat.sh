import json
import shutil
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
        self._tmux_box_width = None
        self._tmux_line_buf: str = ""

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
                model_name = msg.get("model_key", self.client.current_model_key)
                title = f"[bold green]You & {model_name}[/bold green]"
                border_style = "green" if self.client.borders_enabled else "none"
                content_renderable = Text(msg.get("content", "") or "[No content]", no_wrap=False, overflow="fold")
                self.console.print(Panel(content_renderable, title=title, border_style=border_style, box=self.client.boxStyle), crop=False)

            elif role == "assistant":
                self.console.print(self._create_assistant_panel(msg))

            elif role == "tool":
                output_renderable = Text(msg.get("content", "") or "[No output]", no_wrap=False, overflow="fold")
                border_style = "green" if self.client.borders_enabled else "none"
                self.console.print(Panel(
                    output_renderable,
                    title=f"[bold green]Tool Output: {msg.get('name', 'N/A')}[/bold green]",
                    border_style=border_style,
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
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])
        renderables = []

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

        border_style = "cyan" if self.client.borders_enabled else "none"
        return Panel(
            Group(*renderables),
            title=title,
            border_style=border_style,
            box=self.client.boxStyle
        )

    # Streaming API centralization
    def begin_stream(self, model_name: str, mode: str):
        self._stream_mode = mode
        self._stream_started = False
        self._tmux_line_buf = ""
        if mode == "normal":
            from rich.live import Live
            self._live = Live(console=self.console, auto_refresh=False, vertical_overflow="visible")
            self._live.__enter__()
            self._live.update(self.create_live_display(None, {}), refresh=True)
        elif mode == "tmux":
            # Always show a header, even if borders are off
            if self.client.borders_enabled:
                import shutil
                from rich import box as _box
                width = shutil.get_terminal_size((80, 20)).columns
                self._tmux_box_width = max(20, width - 2)
                if self.client.boxStyle is _box.ROUNDED:
                    tl, tr, h = "╭", "╮", "─"
                else:
                    tl, tr, h = "┌", "┐", "─"
                inner_width = self._tmux_box_width - 2
                title = f" Assistant ({model_name}) "
                if len(title) > inner_width:
                    title = title[:max(0, inner_width)]
                pad_left = max(0, (inner_width - len(title)) // 2)
                pad_right = max(0, inner_width - len(title) - pad_left)
                self.console.print(f"{tl}{h * pad_left}{title}{h * pad_right}{tr}")
            else:
                # Header without borders
                self.console.print(f"--- Assistant ({model_name}) ---")

    def _emit_tmux_wrapped_lines(self, text: str):
        if text is None:
            return
        self._tmux_line_buf += text
        width = (self._tmux_box_width - 2) if (self._tmux_box_width and self.client.borders_enabled) else None

        def emit_line(line: str):
            if self._tmux_box_width and self.client.borders_enabled:
                avail = self._tmux_box_width - 2
                self.console.print(f"│{line.ljust(avail)}│")
            else:
                self.console.print(line)

        while True:
            if "\n" in self._tmux_line_buf:
                line, self._tmux_line_buf = self._tmux_line_buf.split("\n", 1)
                if width:
                    start = 0
                    while start < len(line):
                        segment = line[start: start + width + 1]
                        if len(segment) <= width:
                            emit_line(segment)
                            break
                        cut = segment.rfind(" ", 0, width)
                        if cut == -1:
                            cut = width
                        emit_line(segment[:cut])
                        start += cut
                else:
                    emit_line(line)
                self._stream_started = True
            else:
                if width and len(self._tmux_line_buf) > width:
                    segment = self._tmux_line_buf[: width + 1]
                    cut = segment.rfind(" ", 0, width)
                    if cut == -1:
                        cut = width
                    emit_line(self._tmux_line_buf[:cut])
                    self._tmux_line_buf = self._tmux_line_buf[cut:]
                    self._stream_started = True
                    continue
                break

    def stream_chunk(self, content: Optional[str] = None, reasoning: Optional[str] = None, tool_calls_delta: Optional[Dict] = None, model_name: Optional[str] = None, buffers: Optional[Dict] = None):
        if self._stream_mode == "normal":
            if not self._live:
                return
            # Build a stable panel that includes content + tool calls preview
            assistant_buf = {
                "content": "".join(buffers.get("assistant_text_parts", [])) if buffers else "",
                "tool_calls": list((buffers.get("tool_calls_buf") or {}).values())
            }
            self._live.update(self.create_live_display("".join(buffers.get("reasoning_parts", [])) if buffers else None, assistant_buf), refresh=True)
        elif self._stream_mode == "tmux":
            if content:
                self._emit_tmux_wrapped_lines(content)
            # Show tool calls headers as they accumulate
            if buffers and buffers.get("tool_calls_buf"):
                # Render a simple tool calls section once per update
                tc_list = list(buffers["tool_calls_buf"].values())
                # Print tool call titles and arguments in a readable form
                # We won't reflow previous; we append as visible updates
                for tc in tc_list:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args_str = func.get("arguments", "")
                    if not args_str:
                        continue
                    # Emit a simple labeled line
                    self._emit_tmux_wrapped_lines(f"\n[Tool Call] {name}\n")
                    # If it's bash/python, try to show script body plainly
                    try:
                        import json as _json
                        parsed = _json.loads(args_str)
                        script = parsed.get("script", "")
                        if script:
                            for line in script.splitlines():
                                self._emit_tmux_wrapped_lines(line + "\n")
                        else:
                            self._emit_tmux_wrapped_lines(args_str + "\n")
                    except Exception:
                        self._emit_tmux_wrapped_lines(args_str + "\n")

    def end_stream(self, final_assistant_msg: Dict):
        mode = self._stream_mode
        self._stream_mode = None
        if mode == "normal":
            # Close Live first to avoid redraw conflicts, then print final once
            if self._live:
                try:
                    self._live.__exit__(None, None, None)
                except Exception:
                    pass
                self._live = None
            self.console.print(self._create_assistant_panel(final_assistant_msg, live_model_name=self.client.current_model_key))
        elif mode == "tmux":
            # Flush any remaining buffered text as final lines
            if self._tmux_line_buf:
                width = (self._tmux_box_width - 2) if (self._tmux_box_width and self.client.borders_enabled) else None
                def emit_line(line: str):
                    if self._tmux_box_width and self.client.borders_enabled:
                        avail = self._tmux_box_width - 2
                        self.console.print(f"│{line.ljust(avail)}│")
                    else:
                        self.console.print(line)
                line = self._tmux_line_buf
                if width:
                    start = 0
                    while start < len(line):
                        segment = line[start: start + width + 1]
                        if len(segment) <= width:
                            emit_line(segment)
                            break
                        cut = segment.rfind(" ", 0, width)
                        if cut == -1:
                            cut = width
                        emit_line(segment[:cut])
                        start += cut
                else:
                    emit_line(line)
                self._tmux_line_buf = ""
                self._stream_started = True
            # Draw bottom border only if we drew a header and content started
            if self.client.borders_enabled and self._tmux_box_width and self._stream_started:
                from rich import box as _box
                if self.client.boxStyle is _box.ROUNDED:
                    bl, br, h = "╰", "╯", "─"
                else:
                    bl, br, h = "└", "┘", "─"
                self.console.print(f"{bl}{h * (self._tmux_box_width - 2)}{br}")
            self._tmux_box_width = None

    def create_live_display(self, reasoning: Optional[str], assistant_msg: Dict) -> Group:
        renderables = []
        
        # Always show a header even without borders in normal mode live
        header_text = f"Assistant ({self.client.current_model_key})"
        if self.client.borders_enabled:
            renderables.append(Panel(
                Text(header_text),
                border_style="cyan",
                box=self.client.boxStyle
            ))
        else:
            renderables.append(Text(f"--- {header_text} ---"))

        if self.client.show_thinking and reasoning:
            renderables.append(Panel(
                Text(reasoning, justify="left", no_wrap=False, overflow="fold"),
                title="[bold magenta]Reasoning[/bold magenta]",
                border_style="magenta",
                box=self.client.boxStyle
            ))

        content = assistant_msg.get("content") or ""
        tool_calls = assistant_msg.get("tool_calls") or []

        if content or tool_calls:
            # Build assistant block with content and tool call previews
            sub_renders = []
            if content:
                sub_renders.append(Text(content, justify="left", no_wrap=False, overflow="fold"))
            if tool_calls:
                from rich.syntax import Syntax
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args_str = func.get("arguments", "")
                    title = f"[bold yellow]Tool Call: {name}[/bold yellow]" if name else "[bold yellow]Tool Call[/bold yellow]"
                    # Try to show code for bash/python
                    try:
                        import json as _json
                        parsed = _json.loads(args_str or "{}")
                        script = parsed.get("script", "")
                        if script and name in ["bash", "python"]:
                            lang = "bash" if name == "bash" else "python"
                            sub_renders.append(Panel(
                                Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True),
                                title=title,
                                border_style="yellow",
                                box=self.client.boxStyle
                            ))
                        else:
                            sub_renders.append(Panel(
                                Text(args_str or "{}", no_wrap=False, overflow="fold"),
                                title=title,
                                border_style="yellow",
                                box=self.client.boxStyle
                            ))
                    except Exception:
                        sub_renders.append(Panel(
                            Text(args_str or "{}", no_wrap=False, overflow="fold"),
                            title=title,
                            border_style="yellow",
                            box=self.client.boxStyle
                        ))
            if sub_renders:
                renderables.append(Panel(
                    Group(*sub_renders),
                    border_style="cyan" if self.client.borders_enabled else "none",
                    box=self.client.boxStyle
                ))
        else:
            renderables.append(Panel(
                "[dim]Assistant is thinking...[/dim]",
                border_style="cyan" if self.client.borders_enabled else "none",
                box=self.client.boxStyle
            ))

        return Group(*renderables)
