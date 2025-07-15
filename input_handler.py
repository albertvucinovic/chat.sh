import os
import sys
import termios
import tty
from io import StringIO
from typing import List, Optional

from completer import Completer
from chat_client import ChatClient

def get_multiline_input(client: ChatClient) -> str:
    completer = Completer(client)

    CLEAR_ENTIRE_LINE = "\x1b[2K"
    MOVE_UP_1 = "\x1b[A"

    def _clear_suggestions():
        """Resets completer state."""
        if completer.active:
            completer.reset()

    print("\n[You]: ", end="", flush=True)
    lines: List[str] = []
    current_line: List[str] = []

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())

        while True:
            char = sys.stdin.read(1)

            # Ctrl+D - submit
            if not char or ord(char) == 4:
                if current_line:
                    lines.append("".join(current_line))
                print()
                break

            # Ctrl+C - save & quit
            elif ord(char) == 3:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                print("\nSaving chat and exiting...")
                if current_line or lines:
                    if current_line:
                        lines.append("".join(current_line))
                    final_input = "\n".join(lines)
                    if final_input.strip():
                        client.messages.append({"role": "user", "content": final_input})
                saved_path = client.save_chat()
                print(f"Chat saved to: {saved_path}")
                sys.exit(0)

            # Ctrl+E - clear
            elif ord(char) == 5:
                _clear_suggestions()
                sys.stdout.write("\r" + CLEAR_ENTIRE_LINE)
                for _ in range(len(lines)):
                    sys.stdout.write(MOVE_UP_1)
                    sys.stdout.write(CLEAR_ENTIRE_LINE)
                lines.clear()
                current_line.clear()
                sys.stdout.write("[You]: ")
                sys.stdout.flush()
                continue

            # Tab or Shift+Tab (Esc [ Z)
            elif char == "\t" or char == "\x1b":
                if char == "\x1b":
                    next_chars = sys.stdin.read(2)
                    if next_chars != "[Z":
                        continue
                    is_forward = False
                else:
                    is_forward = True

                if not completer.active:
                    completer.find_suggestions(current_line)

                suggestion = (
                    completer.next_suggestion()
                    if is_forward
                    else completer.previous_suggestion()
                )

                if suggestion:
                    current_line = completer.apply_suggestion(current_line, suggestion)
                    sys.stdout.write("\r" + CLEAR_ENTIRE_LINE)
                    sys.stdout.write("[You]: " + "".join(current_line))

                sys.stdout.flush()
                continue

            _clear_suggestions()

            if char in ("\r", "\n"):
                lines.append("".join(current_line))
                current_line = []
                sys.stdout.write("\r\n")
                sys.stdout.flush()

            # Backspace
            elif ord(char) == 127:
                if current_line:
                    current_line.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()

            else:
                if char.isprintable():
                    current_line.append(char)
                    sys.stdout.write(char)
                    sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return "\n".join(lines)
