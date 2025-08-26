import subprocess
import sys
import os
from io import StringIO
from pathlib import Path
import json

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
            # If the file doesn't exist, touch it
            with open(abs_path, 'w') as f:
                f.write('')

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
                if pos == -1:
                    break
                count = 1
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

def replace_between(file_path: str, start_text: str, end_text: str, new_text: str) -> str:
    """Replace the first block between exact start_text and the first subsequent exact end_text.

    Rules and behavior:
    - Matches are exact (no regex), across the entire file content including newlines.
    - Replaces the region INCLUDING the boundaries (start_text and end_text) with new_text.
    - Always uses the FIRST occurrence of start_text, and then the FIRST occurrence of end_text
      that appears AFTER the end of that start_text match.
    - Works across line boundaries (start/end markers can span multiple lines).
    - If start_text or the subsequent end_text is not found, returns a clear error without modifying the file.

    Parameters:
    - file_path: path to the file to edit.
    - start_text: exact starting boundary to match (string literal, not regex).
    - end_text: exact ending boundary to match (string literal, not regex). The first one occurring after start_text is used.
    - new_text: the replacement text that will replace the entire matched region (boundaries included).
    """
    try:
        # Resolve and protect system paths
        abs_path = Path(file_path).resolve()
        system_dirs = ["/etc", "/usr", "/var", "/sys", "/boot", "/dev"]
        if any(str(abs_path).startswith(d) for d in system_dirs):
            return "Error: Cannot edit system files in protected directories!"

        # Load content (empty content if file missing, but we won't write unless we succeed)
        if abs_path.exists():
            with open(abs_path, 'r', newline='') as f:
                content = f.read()
        else:
            content = ""

        if not start_text:
            return "Error: start_text cannot be empty."
        if not end_text:
            return "Error: end_text cannot be empty."

        start_idx = content.find(start_text)
        if start_idx == -1:
            return "Error: start_text not found in file. No changes made."
        search_from = start_idx + len(start_text)
        end_idx = content.find(end_text, search_from)
        if end_idx == -1:
            return "Error: end_text not found after start_text. No changes made."

        # Replace inclusive range [start_idx .. end_idx+len(end_text))
        new_content = content[:start_idx] + new_text + content[end_idx + len(end_text):]

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(abs_path, 'w', newline='') as f:
            f.write(new_content)

        return (
            f"Success: Replaced region including boundaries at offsets "
            f"[{start_idx}..{end_idx + len(end_text)}) in {file_path}."
        )
    except Exception as e:
        return f"Error: {str(e)}"

def run_javascript(args: dict) -> str:
    """
    Execute a JS snippet in a Chrome/Chromium instance that is already running
    with `--remote-debugging-port=9222`.
    * If `url` is supplied, the function tries to locate a tab whose current URL
      exactly matches that string (including scheme, host, path and query).
      You can also request “query‑parameter exact” matching by setting
      `url_match_mode` to "exact_query".
    * If no such tab exists (and `url` is non‑empty), a new tab is opened and
      navigated to the supplied URL before running the script.
    * Returns a JSON string: {"result": <script‑return‑value>} or an error message.
    """
    import json
    from urllib.parse import urlparse, parse_qsl
    script = args.get("script", "")
    url_filter = args.get("url", "").strip()
    # Optional mode: "exact" (default) or "exact"
    url_match_mode = args.get("url_match_mode", "exact_query")  # new optional key
    if not script:
        return json.dumps({"error": "No JavaScript `script` supplied to run_javascript."})
    # -------------------------------------------------------------
    # 1)  Load Selenium (fallback to Playwright if you prefer)
    # -------------------------------------------------------------
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
    except Exception as e:
        return json.dumps({"error": f"Selenium (or webdriver‑manager) not installed – {e}"})
    # -------------------------------------------------------------
    # 2)  Attach to the existing Chrome instance started with
    #    `--remote-debugging-port=9222`
    # -------------------------------------------------------------
    try:
        chrome_options = Options()
        chrome_options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
    except Exception as e:
        return json.dumps(
            {
                "error": (
                    f"Could not attach to Chrome on port 9222 – {e}. "
                    "Make sure Chrome is launched with `--remote-debugging-port=9222`."
                )
            }
        )
    # -------------------------------------------------------------
    # 3)  Choose an existing tab that matches the URL filter, or open a new one.
    # -------------------------------------------------------------
    try:
        original_handles = driver.window_handles
        target_handle = None
        print(f"url_filter: {url_filter}")
        if url_filter:
            print("312")
            # Parse the filter once – helps with exact or query‑exact matches.
            filter_parsed = urlparse(url_filter)
            for h in original_handles:
                print(f"handle: {h}")
                driver.switch_to.window(h)
                current_url = driver.current_url or ""
                print(f"current_url: {current_url}")
                # --- BEGIN FIXED SECTION -----------------------------------
                # Exact full‑URL match:
                if url_match_mode == "exact":
                    if current_url == url_filter:
                        target_handle = h
                        print("EXACT MATHCHED")
                        break
                # Exact query‑parameter match (ignores ordering of params):
                elif url_match_mode == "exact_query":
                    print("In exact_query part")
                    cand_parsed = urlparse(current_url)
                    # First compare scheme, netloc and path – they must be identical.
                    if (
                        filter_parsed.scheme == cand_parsed.scheme
                        and filter_parsed.netloc == cand_parsed.netloc
                        and filter_parsed.path == cand_parsed.path
                    ):
                        # Convert query strings to dict‑like objects (multidict safe)
                        # if filter_qs is subset of cand_qs, it's ok
                        filter_qs = dict(parse_qsl(filter_parsed.query, keep_blank_values=True))
                        cand_qs = dict(parse_qsl(cand_parsed.query, keep_blank_values=True))
                        cand_qs = dict((k, cand_qs[k]) for k in filter_qs)
                        if filter_qs == cand_qs:
                            target_handle = h
                            print("EXACT_QUERY MATCHED")
                            break
                # ---------------------------------------------------------
            # If we didn't find a match, open a new tab with the desired URL
            if not target_handle:
                print("handle not found")
                driver.get(url_filter)
                new_handles = driver.window_handles
                # The new handle is the one that wasn't present before
                target_handle = next(
                    (h for h in new_handles if h not in original_handles), None
                )
                if target_handle is None:
                    # Fallback: just use the newest handle
                    target_handle = new_handles[-1]
        # If no URL filter or still no target, fall back to the first tab
        if not target_handle:
            if not original_handles:
                return json.dumps({"error": "No Chrome tabs found."})
            target_handle = original_handles[0]
        driver.switch_to.window(target_handle)
    except Exception as e:
        driver.quit()
        return json.dumps({"error": f"While selecting/creating tab: {e}"})
    # -------------------------------------------------------------
    # 4️⃣  Execute the supplied JavaScript and serialise the return value.
    # -------------------------------------------------------------
    try:
        # Selenium's `execute_script` auto‑wraps the snippet in a function.
        # Use `return` in your JS if you need a value.
        result = driver.execute_script(script)
        out = json.dumps({"result": result}, ensure_ascii=False, indent=2)
    except Exception as e:
        out = json.dumps({"error": f"Error during script execution: {e}"})
    finally:
        # Detach from Chrome – do NOT close the browser window.
        driver.quit()
    return out

def tool_search(args: dict) -> str:
    try:
        query = args.get('query', '').strip()
        from tavily import TavilyClient
        client = TavilyClient(os.getenv('TAVILY_API_KEY'))
        return json.dumps(client.search(query=query), indent=3)
    except Exception as e:
        return json.dumps({"error": f"Error during search call execution: {e}"})
