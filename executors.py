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
            return f"Error: File not found at {abs_path}"
        
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

def replace_lines(file_path: str, start_line: int, end_line: int, new_content: str,
                   indexing: str = "boundary", operation: str = "auto", position: str = "after") -> str:
    """Flexible line editing with intuitive options.

    Two indexing modes:
    - indexing = "boundary" (default, backward compatible):
      Boundaries are positions 0..N (N = number of lines).
      • start==end: insert at that boundary (e.g., (0,0) at beginning, (1,1) between 1 and 2, (N,N) append)
      • start<end: replace lines with 1-based indexes in [start+1..end]
    - indexing = "line":
      Use 1-based line numbers directly.
      • operation = "replace" (or auto when start<end): replace inclusive lines [start..end]
      • operation = "insert" (or auto when start==end): insert using position:
          - position = "after" (default) or "before"
          - (0,0): insert at beginning
          - (k,k) with 1<=k<=N: insert before/after line k
          - (N+1,N+1): insert at end (after last line)

    Notes:
    - A missing file is only auto-created when inserting at the beginning: (0,0) with indexing='boundary' or
      indexing='line' and operation='insert'.
    - new_content is split on lines and each line gets a trailing \n.
    """
    try:
        abs_path = Path(file_path).resolve()

        # Normalize options
        idx_mode = (indexing or "boundary").strip().lower()
        op = (operation or "auto").strip().lower()
        pos = (position or "after").strip().lower()
        if idx_mode not in ("boundary", "line"):
            return "Error: indexing must be 'boundary' or 'line'."
        if op not in ("auto", "insert", "replace"):
            return "Error: operation must be 'auto', 'insert', or 'replace'."
        if pos not in ("before", "after"):
            return "Error: position must be 'before' or 'after'."

        # Load file content (allow create-on-insert-at-beginning)
        created_file = False
        if not abs_path.exists():
            if idx_mode == "boundary" and start_line == 0 and end_line == 0:
                lines = []
                created_file = True
            elif idx_mode == "line" and start_line == 0 and end_line == 0 and (op in ("auto", "insert")):
                lines = []
                created_file = True
                op = "insert"
            else:
                return f"Error: File not found at {file_path}"
        else:
            with open(abs_path, 'r', newline='') as f:
                content = f.read()
            lines = content.splitlines(keepends=True)

        N = len(lines)

        # Prepare insert segment
        insert_segment = []
        if new_content:
            insert_segment = [line + "\n" for line in new_content.splitlines()]

        def write_and_report(bound_s: int, bound_e: int, replaced_count: int, note: str = "") -> str:
            new_lines = lines[:bound_s] + insert_segment + lines[bound_e:]
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            with open(abs_path, 'w', newline='') as f:
                f.writelines(new_lines)
            if replaced_count == 0:
                return f"Success: Inserted at boundary {bound_s} in {file_path}.{note}"
            else:
                return f"Success: Replaced {replaced_count} line(s) between boundaries {bound_s} and {bound_e} in {file_path}.{note}"

        # Boundary mode (unchanged semantics, with validation)
        if idx_mode == "boundary":
            if start_line < 0 or end_line < 0:
                return "Error: Line boundaries must be non-negative (0..N)."
            if start_line > end_line:
                return "Error: start_line cannot be greater than end_line."
            if start_line > N or end_line > N:
                return f"Error: boundary out of bounds. File has {N} line(s); valid boundaries are 0..{N}."
            # If user explicitly chose operation, validate coherence
            if op == "insert" and start_line != end_line:
                return "Error: For operation='insert' in boundary mode, start_line must equal end_line."
            if op == "replace" and start_line == end_line:
                return "Error: For operation='replace' in boundary mode, start_line must be less than end_line."
            replaced = max(0, end_line - start_line)
            note = " (file created)" if created_file else ""
            return write_and_report(start_line, end_line, replaced, note)

        # Line-number mode (intuitive)
        # Validate inputs for basic sanity (we allow 0 only for insert-at-beginning)
        if start_line < 0 or end_line < 0:
            return "Error: Line numbers must be non-negative. Use 0 only for insert at beginning."
        if start_line > end_line:
            return "Error: start_line cannot be greater than end_line."

        # Determine operation if auto
        if op == "auto":
            op = "insert" if start_line == end_line else "replace"

        if op == "replace":
            if start_line < 1 or end_line < 1:
                return "Error: For operation='replace' with indexing='line', lines must be >= 1."
            if end_line > N:
                return f"Error: end_line out of bounds. File has {N} line(s)."
            # Convert line range [L1..L2] to boundary [L1-1 .. L2]
            b_start = start_line - 1
            b_end = end_line
            replaced = end_line - start_line + 1
            return write_and_report(b_start, b_end, replaced)

        # op == 'insert'
        # Determine target boundary from line number and position
        if start_line == 0:  # insert at beginning
            b = 0
        else:
            # Allow N+1 as a synonym for append-at-end (after last line)
            if start_line == N + 1:
                b = N
            else:
                if start_line < 1 or start_line > N:
                    return f"Error: Cannot insert relative to line {start_line}; file has {N} line(s)."
                if pos == "after":
                    b = start_line  # after line k -> boundary k
                else:
                    b = start_line - 1  # before line k -> boundary k-1
        return write_and_report(b, b, 0)

    except Exception as e:
        return f"Error: {str(e)}"
