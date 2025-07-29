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
        
        # Read entire file content as a single string with original newlines
        with open(abs_path, 'r', newline='') as f:
            content = f.read()
        
        # Track replacements
        replacements = []
        
        # Perform replacement
        if line_number:
            # Convert line number to character position range
            lines = content.splitlines(keepends=True)
            if line_number < 1 or line_number > len(lines):
                return f"Invalid line number: {line_number} (file has {len(lines)} lines)"
            
            # Calculate start and end positions for the line
            line_start = 0
            for i in range(line_number-1):
                line_start += len(lines[i])
            
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
            with open(abs_path, 'w', newline='') as f:
                f.write(new_content)
        else:
            # Full file replacement - search entire content
            if old_str in content:
                new_content = content.replace(old_str, new_str)
                # Count occurrences
                count = 0
                start = 0
                while start < len(new_content):
                    pos = new_content.find(new_str, start)
                    if pos == -1: break
                    count += 1
                    start = pos + len(new_str)
                replacements.append(f"{count} location(s)")
            else:
                # Provide more debug info
                return (f"String not found in file. Old string length: {len(old_str)}, " 
                        f"File length: {len(content)}, First 50 chars of old string: {repr(old_str[:50])}, "
                        f"First 100 chars of file: {repr(content[:100])}")
            
            # Write changes
            with open(abs_path, 'w', newline='') as f:
                f.write(new_content)
        
        return f"Success! Replaced in {', '.join(replacements)}"
    except Exception as e:
        return f"Error: {str(e)}"
