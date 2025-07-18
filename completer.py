import os
import re
import glob
from typing import Iterable, List

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


class ChatClient:
    pass  # Forward declaration for type hinting


class PtkCompleter(Completer):
    def __init__(self, client: "ChatClient"):
        self.client = client

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        """Provides filesystem suggestions for a given prefix, handling '~'."""
        try:
            expanded_prefix = os.path.expanduser(prefix)
            matches = glob.glob(expanded_prefix + '*')
            suggestions = []
            for match in matches:
                normalized_match = match.replace('\\', '/')
                if os.path.isdir(normalized_match):
                    suggestions.append(normalized_match + '/')
                else:
                    suggestions.append(normalized_match)
            return suggestions
        except (OSError, PermissionError):
            return []

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        """
        The main completion logic, structured as a clear if/elif/else chain
        to ensure only one completion type is active at a time.
        """
        text = document.text_before_cursor

        # --- Command-Specific Handlers ---

        # Handler for: o <chat_file>
        if text.startswith("o "):
            prefix = text[len("o "):]
            # For 'o', we want both chat files and regular files.
            # So we let it fall through to the general completer,
            # but first add our specialized suggestions.
            suggestions = set()
            try:
                chat_files = [f.name for f in self.client.chat_dir.iterdir(
                ) if f.name.startswith(prefix) and f.suffix == ".json"]
                for f_name in chat_files:
                    suggestions.add(f_name)
            except OSError:
                pass

            # Also get general filesystem suggestions
            fs_suggestions = self._get_filesystem_suggestions(prefix)
            for s in fs_suggestions:
                suggestions.add(s)

            for s in sorted(list(suggestions)):
                yield Completion(s, start_position=-len(prefix))
            return  # Explicit return to stop processing

        # Handler for: /model <model_key>
        elif text.startswith("/model "):
            prefix = text[len("/model "):]
            if self.client.models_config:
                for name in self.client.models_config.keys():
                    if name.startswith(prefix):
                        yield Completion(name, start_position=-len(prefix))
            return  # Explicit return

        # Handler for: /global/ <command_file>
        elif text.startswith("/global/"):
            prefix = text[len("/global/"):]
            script_dir = os.path.dirname(os.path.realpath(__file__))
            global_dir = os.path.join(script_dir, 'global_commands')
            search_path = os.path.join(global_dir, prefix)
            suggestions = self._get_filesystem_suggestions(search_path)
            for s in suggestions:
                rel_path = os.path.relpath(s, global_dir).replace('\\', '/')
                yield Completion(rel_path, start_position=-len(prefix))
            return  # Explicit return

        # --- General Fallback Logic for Filesystem Paths ---
        else:
            # This logic runs for 'b ...' and any other command.
            parts = text.split()
            if not parts:
                return  # Nothing to complete

            # If the line ends with a space, the user has finished a word.
            # We could offer suggestions for the current directory, but for now we'll do nothing.
            if text.endswith(' '):
                return

            prefix_to_complete = parts[-1]
            suggestions = self._get_filesystem_suggestions(prefix_to_complete)

            # This is the crucial fix for the "toggling" bug:
            # Do not suggest the prefix itself if it's the only option and no changes are made.
            if len(suggestions) == 1 and suggestions[0].lower() == prefix_to_complete.lower():
                return

            for s in suggestions:
                yield Completion(s, start_position=-len(prefix_to_complete))
