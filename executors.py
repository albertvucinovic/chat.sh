import subprocess
import sys
import os
from io import StringIO
from pathlib import Path

def run_bash_script(script: str) -> str:
    """Executes a bash script and captures its stdout and stderr."""
    try:
        result = subprocess.run(
            script,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = ""
        if result.stdout:
            output += f"--- STDOUT ---\n{result.stdout.strip()}\n"
        if result.stderr:
            output += f"--- STDERR ---\n{result.stderr.strip()}\n"

        return output.strip() or "--- The command executed successfully and produced no output ---"
    except subprocess.TimeoutExpired:
        return "--- STDERR ---\nError: Command timed out after 60 seconds."
    except Exception as e:
        return f"--- STDERR ---\nError executing command: {e}"

def run_python_script(script: str) -> str:
    """Executes a Python script string and captures its output."""
    old_stdout, old_stderr = sys.stdout, sys.stderr
    redirected_stdout = sys.stdout = StringIO()
    redirected_stderr = sys.stderr = StringIO()

    try:
        exec(script, globals())
        sys.stdout, sys.stderr = old_stdout, old_stderr

        output = ""
        stdout_val = redirected_stdout.getvalue().strip()
        stderr_val = redirected_stderr.getvalue().strip()

        if stdout_val:
            output += f"--- STDOUT ---\n{stdout_val}\n"
        if stderr_val:
            output += f"--- STDERR ---\n{stderr_val}\n"

        return output.strip() or "--- The script executed successfully and produced no output ---"
    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        return f"--- STDERR ---\nError executing Python script: {e}"

def str_replace_editor(file_path: str, old_str: str, new_str: str) -> str:
    """Replace exact string match in file with optional line context"""
    try:
        # Convert to absolute path
        cwd = Path(os.getcwd())
        abs_path = Path(cwd/file_path).resolve()
        # Prevent editing system files
        system_dirs = ["/etc", "/usr", "/var", "/sys", "/boot", "/dev"]
        if any(str(abs_path).startswith(d) for d in system_dirs):
            return f"Error: Cannot edit system files in protected directories!"
        # Verify file exists
        if not abs_path.exists():
            #If the file doesn't exist, touch it
            with open(abs_path, 'w') as f:
                f.write('')
            #return f"Error: File not found at {abs_path}"

        # Read entire file content as a single string with original newlines
        with open(abs_path, 'r', newline='') as f:
            content = f.read()

        # Track replacements
        replacements = []

        # Full file replacement - search entire content
        if old_str == "":
            new_content = new_str + content
        elif old_str in content:
            new_content = content.replace(old_str, new_str)
            # Count occurrences
            count = 0
            start = 0
            while start < len(new_content):
                pos = new_content.find(new_str, start)
                if pos == -1: break
                count  = 1
                start = pos + len(new_str)
            replacements.append(f"{count} location(s)")
        else:
            # Find longest substring starting with old_str
            longest_match = ""
            start_index = 0

            while start_index < len(content):
                # Find next occurrence of old_str's first character
                start_index = content.find(old_str[0], start_index)
                if start_index == -1:
                    break

                # Check how many consecutive characters match
                match_length = 0
                for i in range(len(old_str)):
                    if start_index + i >= len(content):
                        break
                    if content[start_index + i] != old_str[i]:
                        break
                    match_length += 1

                # Update longest match found
                if match_length > len(longest_match):
                    longest_match = content[start_index:start_index + match_length]

                # Move to next position
                start_index += 1

            # Prepare error message with longest match
            if longest_match:
                # Find context around the longest match
                match_index = content.find(longest_match)
                context_start = max(0, match_index - 20)
                context_end = min(len(content), match_index + len(longest_match) + 20)
                context = content[context_start:context_end]

                return (
                    f"String not found in file. Found longest match starting with old string: {len(longest_match)} characters.\n"
                    f"Longest match: {repr(longest_match)}\n"
                    f"Context in file:\n{repr(context)}"
                )
            else:
                return (
                    f"String not found in file. Old string length: {len(old_str)}, "
                    f"File length: {len(content)}, First 100 chars of old string: {repr(old_str[:100])}, "
                    f"First 200 chars of file: {repr(content[:200])}"
                )

        # Write changes
        with open(abs_path, 'w', newline='') as f:
            f.write(new_content)

        return f"Success! Replaced in {', '.join(replacements)}"
    except Exception as e:
        return f"Error: {str(e)}"

def replace_lines(file_path: str, start_line: int, end_line: int | None = None, new_content: str = "",
                   action: str = "replace", position: str = "after") -> str:
    """Simple, line-number based editing.

    Semantics (1-based lines):
    - action="replace": replace inclusive lines [start_line..end_line]. If end_line is None, replace only start_line.
    - action="insert": insert new_content relative to start_line using position ("before"|"after").
        • Insert at beginning: start_line=1 with position="before" (works for missing files — file will be created).
        • Append at end: start_line=N with position="after" (also accepts start_line==N+1).
        • end_line is ignored for insert.
    - action="delete": delete inclusive [start_line..end_line]. If end_line is None, delete only start_line.

    Notes:
    - File must exist for replace/delete. For insert, missing file is allowed only if inserting at beginning (before line 1).
    - new_content is split by lines; each inserted line is written with a trailing
.
    - Returns a short success message describing the change, or an error.
    """
    try:
        abs_path = Path(file_path).resolve()

        act = (action or "replace").strip().lower()
        pos = (position or "after").strip().lower()
        if act not in ("replace", "insert", "delete"):
            return "Error: action must be one of: replace, insert, delete."
        if pos not in ("before", "after"):
            return "Error: position must be 'before' or 'after'."

        # Load file if present
        if abs_path.exists():
            with open(abs_path, 'r', newline='') as f:
                content = f.read()
            lines = content.splitlines(keepends=True)
        else:
            lines = []

        N = len(lines)

        # Helpers
        def build_insert_segment(s: str):
            if not s:
                return []
            return [line + "\n" for line in s.splitlines()]

        def write(lines_out):
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(abs_path, 'w', newline='') as f:
                f.writelines(lines_out)

        # Validate and execute per action
        if act == "insert":
            if N == 0 and start_line == 1 and pos in ("before", "after"):
                # create new file with content
                new_lines = build_insert_segment(new_content)
                write(new_lines)
                return f"Success: Inserted at beginning in {file_path}. (file created)"
            if not abs_path.exists():
                return f"Error: File not found at {file_path}. Insert into a missing file is only allowed at beginning (before line 1)."
            if start_line < 1:
                return "Error: start_line must be >= 1 for insert."
            if start_line > N + 1:
                return f"Error: start_line out of bounds for insert. File has {N} line(s)."
            # Map to boundary b
            if start_line == N + 1:
                b = N  # append
            else:
                b = start_line if pos == "after" else (start_line - 1)
            seg = build_insert_segment(new_content)
            new_lines = lines[:b] + seg + lines[b:]
            write(new_lines)
            where = "after" if b == start_line else ("before" if b == start_line - 1 else "end")
            return f"Success: Inserted {where} line {start_line} in {file_path}."

        if start_line is None or start_line < 1:
            return "Error: start_line must be >= 1."
        if end_line is None:
            end_line = start_line
        if end_line < start_line:
            return "Error: end_line cannot be less than start_line."
        # Convert to 0-based indices
        s_idx = start_line - 1
        e_idx = end_line      # slice end is exclusive

        if act == "replace":
            if not abs_path.exists():
                #touch the file
                with open(abs_path, 'w') as f:
                    f.write("")
            seg = build_insert_segment(new_content)
            new_lines = lines[:s_idx] + seg + lines[e_idx:]
            write(new_lines)
            count = end_line - start_line + 1
            return f"Success: Replaced {count} line(s) [{start_line}-{end_line}] in {file_path}."
        elif act == "delete":
            # Delete require file exists
            if not abs_path.exists():
                return f"Error: File not found at {file_path}"
            new_lines = lines[:s_idx] + lines[e_idx:]
            write(new_lines)
            count = end_line - start_line + 1
            return f"Success: Deleted {count} line(s) [{start_line}-{end_line}] in {file_path}."
        else:
            return "Error: Unknown action."
    except Exception as e:
        return f"Error: {str(e)}"

def tool_js_console(args: dict) -> str:
    """
    Execute a JS snippet in a Chrome/Chromium tab that is already running
    with `--remote-debugging-port=9222`.
    Returns a JSON‑encoded string with the result or an error message.
    """
    script = args.get("script", "")
    url_filter = args.get("url", "").strip()
    if not script:
        return "Error: No JavaScript `script` supplied to js_console."
    # -------------------------------------------------------------
    # 1️⃣  Try Selenium first (fallback to Playwright if you prefer)                                                                                       │
    # -------------------------------------------------------------
    try:
        # Lazy‑import so the rest of the program works even if Selenium is missing.
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        # webdriver‑manager will download the correct driver for you.
        from webdriver_manager.chrome import ChromeDriverManager
    except Exception as e:
        return f"Error: Selenium (or webdriver‑manager) not installed – {e}"
    # -------------------------------------------------------------
    # 2️⃣  Connect to the existing Chrome instance that was started                                                                                        │
    #    with `--remote-debugging-port=9222`.
    # -------------------------------------------------------------
    try:
        chrome_options = Options()
        # This tells Selenium to *attach* to the running Chrome instead of launching a new one.
        chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
    except Exception as e:
        return f"Error: Could not attach to Chrome on port 9222 – {e}\n" \
               "Make sure Chrome is launched with `--remote-debugging-port=9222`."
    # -------------------------------------------------------------
    # 3️⃣  (Optional) Pick the tab that matches `url` if provided.                                                                                         │
    # -------------------------------------------------------------
    try:
        # Selenium treats each tab as a window handle.
        handles = driver.window_handles
        target_handle = None
        if url_filter:
            for h in handles:
                driver.switch_to.window(h)
                current_url = driver.current_url or ""
                if url_filter in current_url:
                    target_handle = h
                    break
        # Fallback to the first tab.
        if not target_handle:
            target_handle = handles[0] if handles else None
        if target_handle:
            driver.switch_to.window(target_handle)
        else:
            return "Error: No Chrome tabs found."
    except Exception as e:
        # Even if tab selection fails we still want to close the driver cleanly.
        driver.quit()
        return f"Error while selecting tab: {e}"
    # -------------------------------------------------------------
    # 4️⃣  Execute the supplied JavaScript.                                                                                                                │
    # -------------------------------------------------------------
    try:
        # Selenium's `execute_script` automatically wraps the snippet in a function.
        # If the user wants a return value they must use the `return` keyword.
        result = driver.execute_script(script)
        # Serialise the Python result to JSON so the LLM can parse it reliably.
        # Most browsers will return primitives, objects become dicts, arrays become lists.
        out = json.dumps({"result": result}, ensure_ascii=False, indent=2)
    except Exception as e:
        out = f"Error during script execution: {e}"
    finally:
        # We *do not* quit the driver here – it only detaches, so no Chrome window is closed.
        driver.quit()
    return out
