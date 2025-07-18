import os
import re
import glob
from typing import Iterable, List, Set

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document


class ChatClient:
    pass  # Forward declaration for type hinting


class PtkCompleter(Completer):
    WORD_DELIMITERS = " `~!@#$%^&*()=+[{]}|;:'\",<>"

    def __init__(self, client: "ChatClient"):
        self.client = client
        self.word_regex = re.compile(
            r"[^\s" + re.escape(self.WORD_DELIMITERS) + "]+")

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        try:
            expanded_prefix = os.path.expanduser(prefix)
            matches = glob.glob(expanded_prefix + '*')
            return [f"{m}/" if os.path.isdir(m) else m for m in matches]
        except (OSError, PermissionError):
            return []

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text_before_cursor = document.text_before_cursor
        word_before_cursor = document.get_word_before_cursor(
            pattern=self.word_regex)

        # --- Command: o <chat_file> ---
        if text_before_cursor.startswith("o "):
            prefix = text_before_cursor[len("o "):]
            try:
                files = [f.name for f in self.client.chat_dir.iterdir(
                ) if f.suffix == ".json" and f.name.startswith(prefix)]
                for f in sorted(files, reverse=True):
                    yield Completion(f, start_position=-len(prefix))
            except OSError:
                pass
            return

        # --- Command: /model <model_key> ---
        if text_before_cursor.startswith("/model "):
            prefix = text_before_cursor[len("/model "):]
            model_keys = self.client.models_config.keys()
            for name in model_keys:
                if name.startswith(prefix):
                    yield Completion(name, start_position=-len(prefix))
            return

        # --- Command: /global/ <command_file> ---
        if text_before_cursor.startswith("/global/"):
            prefix = text_before_cursor[len("/global/"):]
            script_dir = os.path.dirname(os.path.realpath(__file__))
            global_dir = os.path.join(script_dir, 'global_commands')
            search_path = os.path.join(global_dir, prefix)
            suggestions = self._get_filesystem_suggestions(search_path)
            for s in suggestions:
                rel_path = os.path.relpath(s, global_dir).replace('\\', '/')
                yield Completion(rel_path, start_position=-len(prefix))
            return

        # --- Fallback: General Filesystem Path Completion ---
        if word_before_cursor:
            suggestions = self._get_filesystem_suggestions(word_before_cursor)
            for s in suggestions:
                yield Completion(s, start_position=-len(word_before_cursor))
            return

        # --- Default: Filesystem path completion ---
        if os.path.sep in text_before_cursor or text_before_cursor.startswith(('.', '/')):
            for s in self._get_filesystem_suggestions(text_before_cursor):
                yield Completion(s, start_position=0)
            return

        # Fallback to word completion from history (if implemented)
        # For now, this is a placeholder.
