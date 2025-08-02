import json
from collections import OrderedDict
from typing import Dict, List, Optional

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class TmuxBox:
    """Reusable tmux-like box renderer for streaming text with wrapping."""
    def __init__(self, console: Console, title: str, borders_enabled: bool, box_style, width_provider):
        self.console = console
        self.title = title
        self.borders_enabled = borders_enabled
        self.box_style = box_style
        self.width_provider = width_provider  # callable returning full available width (including borders)
        self.started = False
        self.closed = False
        self._line_buf: str = ""

    def _top(self):
        width = self.width_provider()
        if not width or not self.borders_enabled:
            self.console.print(f"--- {self.title} ---")
            return
        inner_width = max(0, width - 2)
        title = f" {self.title} "
        if len(title) > inner_width:
            title = title[:inner_width]
        pad_left = max(0, (inner_width - len(title)) // 2)
        pad_right = max(0, inner_width - len(title) - pad_left)
        tl, tr, h = ("╭", "╮", "─") if str(self.box_style).lower().find("rounded") != -1 else ("┌", "┐", "─")
        self.console.print(f"{tl}{h * pad_left}{title}{h * pad_right}{tr}")

    def _bottom(self):
        width = self.width_provider()
        if not width or not self.borders_enabled:
            return
        bl, br, h = ("╰", "╯", "─") if str(self.box_style).lower().find("rounded") != -1 else ("└", "┘", "─")
        self.console.print(f"{bl}{h * (max(0, width - 2))}{br}")

    def _emit_line(self, line: str):
        width = self.width_provider()
        if self.borders_enabled and width:
            avail = max(0, width - 2)
            self.console.print(f"│{line.ljust(avail)}│")
        else:
            self.console.print(line)

    def _emit_wrapped_line(self, line: str, wrap_width: Optional[int]):
        if wrap_width:
            start = 0
            while start < len(line):
                segment = line[start:start + wrap_width + 1]
                if len(segment) <= wrap_width:
                    self._emit_line(segment)
                    break
                cut = segment.rfind(" ", 0, wrap_width)
                if cut == -1:
                    cut = wrap_width
                self._emit_line(segment[:cut])
                start += cut
        else:
            self._emit_line(line)

    @staticmethod
    def _popline(buf: str):
        parts = buf.split("\n", 1)
        if len(parts) == 2:
            return parts[0], parts[1]
        return buf, ""

    def emit(self, text: Optional[str]):
        if not text or self.closed:
            return
        if not self.started:
            self._top()
            self.started = True
        self._line_buf += text
        width = self.width_provider()
        wrap_width = (width - 2) if (self.borders_enabled and width) else None

        while True:
            if "\n" in self._line_buf:
                line, self._line_buf = self._popline(self._line_buf)
                self._emit_wrapped_line(line, wrap_width)
            else:
                if wrap_width and len(self._line_buf) > wrap_width:
                    segment = self._line_buf[:wrap_width + 1]
                    cut = segment.rfind(" ", 0, wrap_width)
                    if cut == -1:
                        cut = wrap_width
                    self._emit_line(self._line_buf[:cut])
                    self._line_buf = self._line_buf[cut:]
                    continue
                break

    def close(self):
        if self.closed:
            return
        if self._line_buf:
            line = self._line_buf
            self._line_buf = ""
            width = self.width_provider()
            wrap_width = (width - 2) if (self.borders_enabled and width) else None
            self._emit_wrapped_line(line, wrap_width)
        if self.started:
            self._bottom()
        self.closed = True


class BoxSession:
    def __init__(self, box_id: str, box: TmuxBox):
        self.id = box_id
        self.box = box
        self.buffer: str = ""
        self.open_started = False
        self.closed = False
        self.consumed_len: int = 0  # for cumulative strings

    def append(self, text: str):
        if text:
            self.buffer += text

    def append_cumulative(self, full_text: str):
        # Append only the delta since last seen length
        if full_text is None:
            return
        new_part = full_text[self.consumed_len:]
        if new_part:
            self.buffer += new_part
            self.consumed_len = len(full_text)

    def emit_all(self):
        if self.buffer:
            self.box.emit(self.buffer)
            self.buffer = ""

    def close(self):
        if not self.closed:
            self.emit_all()
            self.box.close()
            self.closed = True


class DisplayManager:
    def __init__(self, client: "ChatClient"):
        self.client = client
        self.console = client.console
        self._stream_mode: Optional[str] = None  # "normal" | "tmux" | None
        self._live = None
        # tmux streaming queue state
        self._tmux_box_width: Optional[int] = None
        self._tmux_sessions: "OrderedDict[str, BoxSession]" = OrderedDict()
        self._tmux_active_id: Optional[str] = None

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
                # Pretty summaries remain
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
        return Panel(Group(*renderables), title=title, border_style=border_style, box=self.client.boxStyle)

    # Streaming API centralization
    def begin_stream(self, model_name: str, mode: str):
        self._stream_mode = mode
        if mode == "normal":
            from rich.live import Live
            self._live = Live(console=self.console, auto_refresh=False, vertical_overflow="visible")
            self._live.__enter__()
            self._live.update(self.create_live_display(None, {}), refresh=True)
        elif mode == "tmux":
            def width_provider():
                import shutil
                w = shutil.get_terminal_size((80, 20)).columns
                return max(20, w)
            self._tmux_box_width = width_provider()
            self._tmux_sessions = OrderedDict()
            self._tmux_active_id = None

    def _ensure_session(self, sid: str, title: str) -> BoxSession:
        sess = self._tmux_sessions.get(sid)
        if sess is None:
            box = TmuxBox(self.console, title, self.client.borders_enabled, self.client.boxStyle, lambda: self._tmux_box_width)
            sess = BoxSession(sid, box)
            self._tmux_sessions[sid] = sess
        return sess

    def _activate(self, sid: str):
        if self._tmux_active_id == sid:
            return
        # Close current active before switching
        if self._tmux_active_id is not None:
            cur = self._tmux_sessions.get(self._tmux_active_id)
            if cur:
                cur.close()
        self._tmux_active_id = sid
        # Emit any buffered content for the new active session immediately
        new = self._tmux_sessions.get(sid)
        if new:
            new.emit_all()

    def stream_chunk(self, content: Optional[str] = None, reasoning: Optional[str] = None, tool_calls_delta: Optional[Dict] = None, model_name: Optional[str] = None, buffers: Optional[Dict] = None):
        if self._stream_mode == "normal":
            if not self._live:
                return
            assistant_buf = {
                "content": "".join(buffers.get("assistant_text_parts", [])) if buffers else "",
                "tool_calls": list((buffers.get("tool_calls_buf") or {}).values())
            }
            self._live.update(self.create_live_display("".join(buffers.get("reasoning_parts", [])) if buffers else None, assistant_buf), refresh=True)
        elif self._stream_mode == "tmux":
            enq_order: List[str] = []
            if self.client.show_thinking and reasoning:
                sid = "reasoning"
                sess = self._ensure_session(sid, "Reasoning")
                sess.append(reasoning)
                enq_order.append(sid)
            if content:
                sid = "content"
                title = f"Assistant ({model_name or self.client.current_model_key})"
                sess = self._ensure_session(sid, title)
                sess.append(content)
                enq_order.append(sid)
            if buffers and buffers.get("tool_calls_buf"):
                items = buffers["tool_calls_buf"].items() if isinstance(buffers["tool_calls_buf"], dict) else enumerate(buffers["tool_calls_buf"]) 
                for idx, tc in items:
                    func = tc.get("function", {})
                    name = func.get("name", "") or ""
                    args_str = func.get("arguments", "") or ""
                    sid = f"tool:{int(idx)}"
                    title = f"Tool Call: {name}" if name else "Tool Call"
                    sess = self._ensure_session(sid, title)
                    if not sess.box.started and name:
                        sess.box.title = title
                    # Append only delta for cumulative arguments
                    sess.append_cumulative(args_str)
                    enq_order.append(sid)
            if self._tmux_active_id is None and enq_order:
                self._activate(enq_order[0])
            if self._tmux_active_id is not None:
                active = self._tmux_sessions.get(self._tmux_active_id)
                if active:
                    active.emit_all()
                for sid in enq_order:
                    if sid != self._tmux_active_id:
                        self._activate(sid)
                        break

    def end_stream(self, final_assistant_msg: Dict):
        mode = self._stream_mode
        self._stream_mode = None
        if mode == "normal":
            if self._live:
                try:
                    self._live.__exit__(None, None, None)
                except Exception:
                    pass
                self._live = None
            self.console.print(self._create_assistant_panel(final_assistant_msg, live_model_name=self.client.current_model_key))
        elif mode == "tmux":
            if self._tmux_active_id is not None:
                cur = self._tmux_sessions.get(self._tmux_active_id)
                if cur:
                    cur.close()
                self._tmux_active_id = None
            for sid, sess in list(self._tmux_sessions.items()):
                if not sess.closed:
                    sess.close()
            self._tmux_sessions.clear()
            self._tmux_box_width = None

    def create_live_display(self, reasoning: Optional[str], assistant_msg: Dict) -> Group:
        renderables = []
        header_text = f"Assistant ({self.client.current_model_key})"
        if self.client.borders_enabled:
            renderables.append(Panel(Text(header_text), border_style="cyan", box=self.client.boxStyle))
        else:
            renderables.append(Text(f"--- {header_text} ---"))
        if self.client.show_thinking and reasoning:
            renderables.append(Panel(Text(reasoning, justify="left", no_wrap=False, overflow="fold"), title="[bold magenta]Reasoning[/bold magenta]", border_style="magenta", box=self.client.boxStyle))
        content = assistant_msg.get("content") or ""
        tool_calls = assistant_msg.get("tool_calls") or []
        if content or tool_calls:
            sub_renders = []
            if content:
                sub_renders.append(Text(content, justify="left", no_wrap=False, overflow="fold"))
            if tool_calls:
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args_str = func.get("arguments", "")
                    title = f"[bold yellow]Tool Call: {name}[/bold yellow]" if name else "[bold yellow]Tool Call[/bold yellow]"
                    try:
                        import json as _json
                        parsed = _json.loads(args_str or "{}")
                        script = parsed.get("script", "")
                        if script and name in ["bash", "python"]:
                            lang = "bash" if name == "bash" else "python"
                            sub_renders.append(Panel(Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True), title=title, border_style="yellow", box=self.client.boxStyle))
                        else:
                            sub_renders.append(Panel(Text(args_str or "{}", no_wrap=False, overflow="fold"), title=title, border_style="yellow", box=self.client.boxStyle))
                    except Exception:
                        sub_renders.append(Panel(Text(args_str or "{}", no_wrap=False, overflow="fold"), title=title, border_style="yellow", box=self.client.boxStyle))
            if sub_renders:
                renderables.append(Panel(Group(*sub_renders), border_style="cyan" if self.client.borders_enabled else "none", box=self.client.boxStyle))
        else:
            renderables.append(Panel("[dim]Assistant is thinking...[/dim]", border_style="cyan" if self.client.borders_enabled else "none", box=self.client.boxStyle))
        return Group(*renderables)
