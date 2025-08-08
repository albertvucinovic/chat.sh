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

def replace_lines(file_path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Boundary-based replace/insert for text files.

    Semantics:
    - start_line and end_line are boundary indexes in [0..N], where N = number of lines in the file.
      Boundary 0 = before the first line.
      Boundary 1 = after line 1 (between line 1 and 2).
      ... up to N = after the last line.
    - If start_line == end_line: INSERT new_content at that boundary (no lines removed).
      Examples: (0,0) insert at beginning; (1,1) insert between line 1 and 2; (N,N) append at end.
    - If start_line < end_line: REPLACE the block of lines between the boundaries:
      Lines with 1-based indexes in [start_line+1 .. end_line] are replaced.
      Examples: (0,1) replaces line 1; (1,3) replaces lines 2..3.

    Validation:
    - 0 <= start_line <= end_line <= N, where N is computed from the current file (0 for empty).
    - If the file does not exist and both boundaries are 0, the file will be created with new_content.
      Otherwise, a missing file is an error.
    """
    try:
        abs_path = Path(file_path).resolve()

        created_file = False
        # Read file if it exists; otherwise allow creation for (0,0)
        if not abs_path.exists():
            if start_line == 0 and end_line == 0:
                lines = []
                created_file = True
            else:
                return f"Error: File not found at {file_path}"
        else:
            with open(abs_path, 'r', newline='') as f:
                content = f.read()
            lines = content.splitlines(keepends=True)

        N = len(lines)

        # Validate boundaries
        if start_line < 0 or end_line < 0:
            return "Error: Line boundaries must be non-negative (0..N)."
        if start_line > end_line:
            return "Error: start_line cannot be greater than end_line."
        if start_line > N or end_line > N:
            return f"Error: boundary out of bounds. File has {N} line(s); valid boundaries are 0..{N}."

        # Build inserted segment using \n endings (keeps tool behavior consistent)
        insert_segment = []
        if new_content:
            insert_segment = [line + '\n' for line in new_content.splitlines()]

        # Splice using boundary indices (remove lines [start_line, end_line))
        new_lines = lines[:start_line] + insert_segment + lines[end_line:]

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        with open(abs_path, 'w', newline='') as f:
            f.writelines(new_lines)

        removed = end_line - start_line
        if removed == 0:
            location = f"at boundary {start_line}"
            action = "Inserted"
        else:
            location = f"between boundaries {start_line} and {end_line}"
            action = f"Replaced {removed} line(s)"
        note = " (file created)" if created_file else ""
        return f"Success: {action} {location} in {file_path}.{note}"
    except Exception as e:
        return f"Error: {str(e)}"
