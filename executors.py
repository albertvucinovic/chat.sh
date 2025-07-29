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

        return output.strip() or "--- The command executed successfully and produced no output ---"
    except Exception as e:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        return f"--- STDERR ---\nError executing Python script: {e}"

def str_replace_editor(file_path: str, old_str: str, new_str: str, line_number: int = None) -> str:
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
        
        # Read entire file content as a single string
        with open(abs_path, 'r') as f:
            content = f.read()
        
        # Track replacements
        replacements = []
        
        # Perform replacement
        if line_number:
            # Convert line number to character position range
            lines = content.split('\n')
            if line_number < 1 or line_number > len(lines):
                return f"Invalid line number: {line_number} (file has {len(lines)} lines)"
            
            # Calculate start and end positions for the line
            line_start = 0
            for i in range(line_number-1):
                line_start += len(lines[i]) + 1  # +1 for the newline character
            
            line_end = line_start + len(lines[line_number-1])
            
            # Check if old_str exists within this line
            line_content = content[line_start:line_end]
            if old_str in line_content:
                # Replace within the line
                new_line = line_content.replace(old_str, new_str)
                new_content = content[:line_start] + new_line + content[line_end:]
                replacements.append(f"Line {line_number}")
            else:
                return f"String not found in line {line_number}"
            
            # Write changes
            with open(abs_path, 'w') as f:
                f.write(new_content)
        else:
            # Full file replacement
            if old_str in content:
                new_content = content.replace(old_str, new_str)
                count = content.count(old_str)
                replacements.append(f"{count} location(s)")
            else:
                return "String not found in file"
            
            # Write changes
            with open(abs_path, 'w') as f:
                f.write(new_content)
        
        return f"Success! Replaced in {', '.join(replacements)}"
    except Exception as e:
        return f"Error: {str(e)}"
