import json
from collections import OrderedDict
from typing import Dict, List, Optional, Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


class TmuxBox:
    """Reusable tmux-like box renderer for streaming text with wrapping.
    Avoid Rich markup parsing for streamed lines to prevent MarkupError.
    """
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
            self.console.print(Text(f"--- {self.title} ---"), markup=False)
            return
        inner_width = max(0, width - 2)
        title = f" {self.title} "
        if len(title) > inner_width:
            title = title[:inner_width]
        pad_left = max(0, (inner_width - len(title)) // 2)
        pad_right = max(0, inner_width - len(title) - pad_left)
        tl, tr, h = ("╭", "╮", "─") if str(self.box_style).lower().find("rounded") != -1 else ("┌", "┐", "─")
        self.console.print(Text(f"{tl}{h * pad_left}{title}{h * pad_right}{tr}"), markup=False)

    def _bottom(self):
        width = self.width_provider()
        if not width or not self.borders_enabled:
            return
        bl, br, h = ("╰", "╯", "─") if str(self.box_style).lower().find("rounded") != -1 else ("└", "┘", "─")
        self.console.print(Text(f"{bl}{h * (max(0, width - 2))}{br}"), markup=False)

    def _emit_line(self, line: str):
        width = self.width_provider()
        if self.borders_enabled and width:
            avail = max(0, width - 2)
            content = line.ljust(avail)
            self.console.print(Text(f"│{content}│", no_wrap=False, overflow="fold"), markup=False)
        else:
            self.console.print(Text(line, no_wrap=False, overflow="fold"), markup=False)

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
        if text is None or self.closed:
            return
        if not self.started:
            self._top()
            self.started = True
        if text:
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
        self.closed = False
        self.consumed_len: int = 0  # for cumulative strings

    def append(self, text: str):
        if text is None:
            return
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
        # Display preference: show unescaped tool args for readability
        self.unescape_tool_display: bool = True

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

    # Helper: unescape based on tool name for display only
    def _unescape_for_tool(self, name: str, s: str) -> str:
        if not s:
            return s or ""
        out = s
        # Always map common escapes
        out = out.replace("\\n", "\n").replace("\\t", "\t")
        n = (name or "").lower()
        if n == "bash":
            out = out.replace("\\$", "$").replace("\\`", "`")
            # Light backslash reduction (avoid touching \n/\t already handled)
            out = out.replace("\\\\", "\\")
        elif n == "python":
            out = out.replace('\\"', '"').replace("\\'", "'")
        return out

    def _build_pretty_tool_call_renderables(self, name: str, args_str: str) -> List[Any]:
        """Build a pretty, syntax-highlighted representation of a tool call's arguments.
        Attempts to parse JSON; code-like fields are rendered with Syntax; others as text/json.
        """
        renderables: List[Any] = []
        title = f"[bold yellow]Tool Call: {name}[/bold yellow]"
        # Try to parse JSON
        parsed: Optional[Any] = None
        try:
            parsed = json.loads(args_str or "{}")
        except Exception:
            parsed = None

        def is_code_like(val: str) -> bool:
            if not isinstance(val, str):
                return False
            s = val.strip()
            if "\n" in s:
                return True
            if s.startswith("```") and s.endswith("```"):
                return True
            # heuristics for code-ish content
            code_tokens = [
                "def ", "class ", "import ", "#!/", "#!/usr",
                "$(", ";", "{", "}", " then", " fi", " do", " done",
            ]
            return any(tok in s for tok in code_tokens)

        def language_for(name_: str, key: str, val: str) -> str:
            tool = (name_ or "").lower()
            k = (key or "").lower()
            s = (val or "").strip()
            # direct tool mapping
            if tool in ("bash", "sh"):
                return "bash"
            if tool in ("python",):
                return "python"
            # key-based mapping
            if k in ("script", "bash", "shell", "cmd", "command"):
                # decide between bash or python by content
                if "def " in s or "import " in s or s.startswith("#!/usr/bin/env python"):
                    return "python"
                return "bash"
            if k in ("py", "python_code", "code_py"):
                return "python"
            if k in ("json", "payload", "body"):
                # might be JSON
                try:
                    json.loads(s)
                    return "json"
                except Exception:
                    pass
            # content-based hints
            try:
                obj = json.loads(s)
                if isinstance(obj, (dict, list)):
                    return "json"
            except Exception:
                pass
            if s.startswith("<?xml") or s.startswith("<") and s.endswith(">"):
                return "xml"
            if s.startswith("{") and s.endswith("}"):
                return "json"
            # default
            return "bash" if any(tok in s for tok in ["#!/", "$(", ";"]) else "python" if any(tok in s for tok in ["def ", "import "]) else "text"

        if parsed is None or not isinstance(parsed, dict):
            # Fallback: show raw string
            s = args_str or "{}"
            renderables.append(Panel(Text(s, no_wrap=False, overflow="fold"), title=title, border_style="yellow", box=self.client.boxStyle))
            return renderables

        # Build per-field panels, code fields nicely syntax-highlighted
        sub_renders: List[Any] = []
        for k, v in parsed.items():
            if isinstance(v, str) and is_code_like(v):
                lang = language_for(name, k, v)
                if lang == "text":
                    sub_renders.append(Panel(Text(v, no_wrap=False, overflow="fold"), title=f"[bold]{k}[/bold]", border_style="yellow", box=self.client.boxStyle))
                else:
                    # Strip code fences for better highlighting if present
                    code = v
                    if code.strip().startswith("```") and code.strip().endswith("```"):
                        inner = code.strip().strip("`")
                        # naive fence removal: ```lang\n...```
                        parts = inner.split("\n", 1)
                        code = parts[1] if len(parts) == 2 else parts[0]
                    sub_renders.append(Panel(Syntax(code, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True), title=f"[bold]{k}[/bold]", border_style="yellow", box=self.client.boxStyle))
            else:
                # Pretty-print non-code JSON values
                try:
                    pretty = json.dumps(v, indent=2, ensure_ascii=False) if not isinstance(v, str) else v
                except Exception:
                    pretty = str(v)
                sub_renders.append(Panel(Text(pretty, no_wrap=False, overflow="fold"), title=f"[bold]{k}[/bold]", border_style="yellow", box=self.client.boxStyle))

        if not sub_renders:
            sub_renders.append(Text("{}", no_wrap=False, overflow="fold"))
        renderables.append(Panel(Group(*sub_renders), title=title, border_style="yellow", box=self.client.boxStyle))
        return renderables

    def _render_pretty_tool_calls_only(self, tool_calls: List[Dict]):
        if not tool_calls:
            return
        # Build pretty panels only for tool calls
        sub_panels: List[Any] = []
        for tc in tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "...")
            args_str = func.get("arguments", "")
            sub_panels.extend(self._build_pretty_tool_call_renderables(name, args_str))
        if sub_panels:
            border_style = "cyan" if self.client.borders_enabled else "none"
            self.console.print(Panel(Group(*sub_panels), border_style=border_style, box=self.client.boxStyle))

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

    def _create_assistant_panel(self, msg: Dict, live_model_name: Optional[str] = None, pretty_tool_calls: bool = False) -> Panel:
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
                if pretty_tool_calls:
                    # Final pass: pretty print
                    renderables.extend(self._build_pretty_tool_call_renderables(name, args_str))
                else:
                    # Streaming/raw pass
                    script = None
                    try:
                        parsed = json.loads(args_str or '{}')
                        if isinstance(parsed, dict):
                            script = parsed.get('script')
                    except Exception:
                        script = None
                    if script and name in ["python", "bash"]:
                        lang = name
                        renderables.append(Panel(
                            Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True),
                            title=f"[bold yellow]Tool Call: {name}[/bold yellow]",
                            border_style="yellow",
                            box=self.client.boxStyle
                        ))
                    else:
                        pretty = args_str or ""
                        if self.unescape_tool_display:
                            pretty = self._unescape_for_tool(name, pretty)
                        renderables.append(Panel(
                            Text(pretty, no_wrap=False, overflow="fold"),
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
            last_tool_sid: Optional[str] = None
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
                buf = buffers.get("tool_calls_buf")
                list_items = []
                # Normalize buffer entries into a list of (key_str, tc) where key_str is a stable identifier
                if isinstance(buf, dict):
                    for i, (k, v) in enumerate(buf.items()):
                        if k is None:
                            key_str = (v.get("id") if isinstance(v, dict) else None) or str(i)
                        else:
                            key_str = str(k)
                        list_items.append((key_str, v))
                else:
                    for i, v in enumerate(buf):
                        key_str = (v.get("id") if isinstance(v, dict) else None) or str(i)
                        list_items.append((key_str, v))

                for idx, tc in list_items:
                    func = tc.get("function", {})
                    name = func.get("name", "") or ""
                    args_str = func.get("arguments", "") or ""
                    sid = f"tool:{idx}"
                    title = f"Tool Call: {name}" if name else "Tool Call"
                    sess = self._ensure_session(sid, title)
                    if not sess.box.started and name:
                        sess.box.title = title
                    # Prepare display version based on toggle and tool type
                    display_args = args_str
                    if self.unescape_tool_display:
                        display_args = self._unescape_for_tool(name, args_str)
                    if len(display_args) >= sess.consumed_len:
                        sess.append_cumulative(display_args)
                    else:
                        sess.append(display_args)
                    enq_order.append(sid)
                    last_tool_sid = sid
            # Prefer the most recent tool pane if any updated; else rotate as before
            if last_tool_sid is not None:
                self._activate(last_tool_sid)
            elif self._tmux_active_id is None and enq_order:
                self._activate(enq_order[0])
            if self._tmux_active_id is not None:
                active = self._tmux_sessions.get(self._tmux_active_id)
                if active:
                    active.emit_all()
                if last_tool_sid is None:
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
            # Print only pretty-printed tool call panels, do not redraw content
            tool_calls = final_assistant_msg.get("tool_calls") or []
            self._render_pretty_tool_calls_only(tool_calls)
        elif mode == "tmux":
            # Close all tmux raw panes first
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
            # Print only pretty-printed tool call panels, do not redraw content
            tool_calls = final_assistant_msg.get("tool_calls") or []
            self._render_pretty_tool_calls_only(tool_calls)

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
                        script = parsed.get("script", "") if isinstance(parsed, dict) else ""
                        if script and name in ["bash", "python"]:
                            lang = "bash" if name == "bash" else "python"
                            sub_renders.append(Panel(Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled, word_wrap=True), title=title, border_style="yellow", box=self.client.boxStyle))
                        else:
                            pretty = args_str or "{}"
                            if self.unescape_tool_display:
                                pretty = self._unescape_for_tool(name, pretty)
                            sub_renders.append(Panel(Text(pretty, no_wrap=False, overflow="fold"), title=title, border_style="yellow", box=self.client.boxStyle))
                    except Exception:
                        pretty = args_str or "{}"
                        if self.unescape_tool_display:
                            pretty = self._unescape_for_tool(name, pretty)
                        sub_renders.append(Panel(Text(pretty, no_wrap=False, overflow="fold"), title=title, border_style="yellow", box=self.client.boxStyle))
            if sub_renders:
                renderables.append(Panel(Group(*sub_renders), border_style="cyan" if self.client.borders_enabled else "none", box=self.client.boxStyle))
        else:
            renderables.append(Panel("[dim]Assistant is thinking...[/dim]", border_style="cyan" if self.client.borders_enabled else "none", box=self.client.boxStyle))
        return Group(*renderables)
