import subprocess
import sys
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

        return output.strip() or "--- The command executed successfully and produced no output ---"
    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        return f"--- STDERR ---\nError executing Python script: {e}"

def str_replace_editor(file_path: str, old_str: str, new_str: str, line_number: int = None) -> str:
    """Replace exact string match in file with optional line context"""
    try:
        # Convert to absolute path
        abs_path = Path(file_path).resolve()
        # Verify file exists
        if not abs_path.exists():
            return f"Error: File not found at {abs_path}"
        # Read file content
        with open(abs_path, 'r') as f:
            lines = f.readlines()
        # Track replacements
        replacements = []
        # Perform replacement
        if line_number:
            # Line-specific replacement
            if 1 <= line_number <= len(lines):
                if old_str in lines[line_number-1]:
                    lines[line_number-1] = lines[line_number-1].replace(old_str, new_str)
                    replacements.append(f"Line {line_number}")
                else:
                    return f"String not found in line {line_number}"
            else:
                return f"Invalid line number: {line_number} (file has {len(lines)} lines)"
        else:
            # Full file replacement
            new_lines = []
            replaced = False
            for i, line in enumerate(lines):
                if old_str in line:
                    new_lines.append(line.replace(old_str, new_str))
                    replacements.append(f"Line {i+1}")
                    replaced = True
                else:
                    new_lines.append(line)
            if not replaced:
                return "String not found in file"
            lines = new_lines
        # Write changes
        with open(abs_path, 'w') as f:
            f.writelines(lines)
        return f"Success! Replaced in {len(replacements)} location(s): {', '.join(replacements)}"
    except Exception as e:
        return f"Error: {str(e)}"
