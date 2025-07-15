import os
import sys
import termios
import tty
import signal
from io import StringIO
from typing import List, Optional

from completer import Completer
from chat_client import ChatClient

def get_multiline_input(client: ChatClient) -> str:
    completer = Completer(client)

    CLEAR_ENTIRE_LINE = "\x1b[2K"
    MOVE_UP_1 = "\x1b[A"

    def _clear_suggestions():
        if completer.active:
            completer.reset()

    print("\n[You]: ", end="", flush=True)
    lines: List[str] = []
    current_line: List[str] = []

    # This function now fully manages its own terminal state
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

            # Ctrl+C - Raise KeyboardInterrupt to be caught by the global handler
            elif ord(char) == 3:
                # Re-raise the interrupt signal to be caught by the main loop's handler
                raise KeyboardInterrupt

            # ... (rest of the input handling logic is the same) ...
            elif ord(char) == 5: # Ctrl+E
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
            elif char == "\t" or char == "\x1b": # Tab or Shift+Tab
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
            elif ord(char) == 127: # Backspace
                if current_line:
                    current_line.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            else:
                if char.isprintable():
                    current_line.append(char)
                    sys.stdout.write(char)
                    sys.stdout.flush()

    except KeyboardInterrupt:
        # Ensure terminal is restored before propagating the interrupt
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        # The global signal handler in chat.py will now take over
        os.kill(os.getpid(), signal.SIGINT)

    finally:
        # Always restore the terminal settings when input is finished
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return "\n".join(lines)
