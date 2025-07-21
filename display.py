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
                content_renderable = Text(msg.get("content", "") or "[No content]")
                self.console.print(Panel(content_renderable, title=title, border_style=border_style, box=self.client.boxStyle))

            elif role == "assistant":
                self.console.print(self._create_assistant_panel(msg))

            elif role == "tool":
                output_renderable = Text(msg.get("content", "") or "[No output]")
                self.console.print(Panel(
                    output_renderable,
                    title=f"[bold green]Tool Output: {msg.get('name', 'N/A')}[/bold green]",
                    border_style="green",
                    box=self.client.boxStyle
                ))
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
            renderables.append(Text(content, justify="left"))

        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                name, args_str = func.get("name", "..."), func.get("arguments", "")
                try:
                    script = json.loads(args_str or '{}').get('script', args_str)
                    lang = name if name in ["python", "bash"] else "json"
                    renderables.append(Panel(
                        Syntax(script, lang, theme="monokai", line_numbers=self.client.borders_enabled),
                        title=f"[bold yellow]Tool Call: {name}[/bold yellow]",
                        border_style="yellow",
                        box=self.client.boxStyle
                    ))
                except (json.JSONDecodeError, AttributeError):
                    renderables.append(Panel(
                        Text(args_str),
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

    def create_live_display(self, reasoning: Optional[str], assistant_msg: Dict) -> Group:
        """Creates the renderable Group for the Live display during streaming."""
        renderables = []
        
        # 1. Add the Reasoning panel only if it has content and the setting is enabled.
        if self.client.show_thinking and reasoning:
            renderables.append(Panel(
                Text(reasoning, justify="left"),
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
