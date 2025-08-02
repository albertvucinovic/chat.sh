from __future__ import annotations
import json
import html
from pathlib import Path
from typing import Dict, List, Optional, Any

# Minimal CSS for a striking, readable HTML export
BASE_CSS = r"""
:root {
  --bg: #0b0e14;
  --panel: #11161f;
  --text: #e6e6e6;
  --muted: #9aa4b2;
  --cyan: #35b6ff;
  --magenta: #ff3db1;
  --yellow: #ffd166;
  --green: #44d880;
  --red: #ff6b6b;
  --border: #1f2633;
  --glow: 0 0 15px rgba(53,182,255,0.35);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text); font: 15px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; }
pre, code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
.container { max-width: 1100px; margin: 0 auto; padding: 28px 20px 60px; }
.header { display:flex; align-items:center; gap:12px; margin-bottom: 20px; }
.badge { padding: 3px 8px; border:1px solid var(--border); color: var(--muted); border-radius: 999px; background: #0f131b; }
.title { font-size: 22px; font-weight: 700; letter-spacing: 0.2px; }
.panel { border: 1px solid var(--border); background: var(--panel); border-radius: 14px; padding: 14px 16px; margin: 12px 0; box-shadow: var(--glow); }
.panel .heading { display:flex; align-items:baseline; gap:10px; margin-bottom: 10px; }
.panel .heading .h-label { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
.panel .heading .h-title { font-size: 14px; font-weight: 700; color: var(--cyan); }
.panel.user .h-title { color: var(--green); }
.panel.assistant .h-title { color: var(--cyan); }
.panel.tool .h-title { color: var(--yellow); }
.panel.system .h-title { color: var(--magenta); }
.panel pre { background: #0d1117; border:1px solid #202938; color: #e6e6e6; padding: 12px; border-radius: 10px; overflow:auto; }
.panel code { white-space: pre-wrap; }
.code-block { margin: 8px 0; }
.kv { color: var(--muted); font-size: 13px; margin-top: 6px; }
.sep { height: 1px; background: linear-gradient(90deg, rgba(31,38,51,.0), rgba(31,38,51,1), rgba(31,38,51,.0)); margin: 18px 0; }
.footer { color: var(--muted); font-size: 12px; text-align:center; margin-top: 22px; }
.small { font-size: 12px; color: var(--muted); }
.hl { color: #ffd166; }
"""


def _escape(s: Optional[str]) -> str:
    return html.escape(s or "")


def _render_kv(key: str, val: str) -> str:
    return f'<div class="kv"><span class="hl">{_escape(key)}:</span> {_escape(val)}</div>'


def _render_pre(s: str) -> str:
    return f'<pre><code>{_escape(s)}</code></pre>'


def _render_tool_call(tc: Dict[str, Any]) -> str:
    func = tc.get("function", {}) if isinstance(tc, dict) else {}
    name = func.get("name", "")
    args_str = func.get("arguments", "") or ""
    body_parts: List[str] = []
    # Try to parse JSON to find code-ish script field
    try:
        parsed = json.loads(args_str or "{}")
    except Exception:
        parsed = None
    if isinstance(parsed, dict) and "script" in parsed:
        script = str(parsed.get("script") or "")
        body_parts.append(f'<div class="code-block">{_render_pre(script)}</div>')
    else:
        body_parts.append(_render_pre(args_str))
    return f'''<div class="panel tool">
  <div class="heading"><div class="h-label">Tool Call</div><div class="h-title">{_escape(name or 'Tool')}</div></div>
  {''.join(body_parts)}
</div>'''


def _render_message(msg: Dict[str, Any]) -> str:
    role = msg.get("role")
    if role == "system":
        content = msg.get("content", "")
        return f'''<div class="panel system">
  <div class="heading"><div class="h-label">System</div><div class="h-title">System Prompt</div></div>
  {_render_pre(str(content))}
</div>'''
    elif role == "user":
        model = msg.get("model_key", "")
        content = msg.get("content", "") or ""
        return f'''<div class="panel user">
  <div class="heading"><div class="h-label">User</div><div class="h-title">You &nbsp; {_escape(model)}</div></div>
  <div>{_escape(content)}</div>
</div>'''
    elif role == "assistant":
        model = msg.get("model_key", "")
        content = msg.get("content", "") or ""
        parts: List[str] = []
        if content:
            parts.append(f'<div>{_escape(content)}</div>')
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            parts.append(_render_tool_call(tc))
        body = "\n".join(parts) if parts else '<div class="small">[No content or tool calls]</div>'
        return f'''<div class="panel assistant">
  <div class="heading"><div class="h-label">Assistant</div><div class="h-title">Assistant ({_escape(model)})</div></div>
  {body}
</div>'''
    elif role == "tool":
        name = str(msg.get("name", "Tool"))
        content = str(msg.get("content", "") or "")
        return f'''<div class="panel tool">
  <div class="heading"><div class="h-label">Tool Output</div><div class="h-title">{_escape(name)}</div></div>
  {_render_pre(content)}
</div>'''
    else:
        return f'''<div class="panel">
  <div class="heading"><div class="h-label">Message</div><div class="h-title">{_escape(role or 'message')}</div></div>
  <div>{_escape(str(msg.get('content') or ''))}</div>
</div>'''


def render_chat_to_html(messages: List[Dict[str, Any]], short_recap: Optional[str] = None, title_suffix: str = "") -> str:
    page_title = "Chat Export"
    if short_recap:
        page_title += f" — {short_recap}"
    if title_suffix:
        page_title += f" — {title_suffix}"

    parts: List[str] = []
    for msg in messages:
        parts.append(_render_message(msg))

    body = "\n<div class=\"sep\"></div>\n".join(parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{_escape(page_title)}</title>
<style>
{BASE_CSS}
</style>
</head>
<body>
  <div class="container">
    <div class="header">
      <span class="badge">egg export</span>
      <div class="title">{_escape(page_title)}</div>
    </div>
    {body}
    <div class="footer">Generated by Egg — Entropy Gradient</div>
  </div>
</body>
</html>
"""


def export_chat_file(messages: List[Dict[str, Any]], path: str, short_recap: Optional[str] = None, title_suffix: str = "") -> str:
    html_text = render_chat_to_html(messages, short_recap=short_recap, title_suffix=title_suffix)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_text, encoding="utf-8")
    return str(out.resolve())
