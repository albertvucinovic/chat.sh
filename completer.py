import os
import re
import glob
from typing import Iterable, List, Set

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

# Forward declaration for type hinting


class ChatClient:
    pass


class PtkCompleter(Completer):
    """
    A prompt-toolkit completer that integrates filesystem, history, and
    special command completion.
    """
    WORD_DELIMITERS = " `~!@#$%^&*()=+[{]}|;:'\",<>"

    def __init__(self, client: "ChatClient"):
        self.client = client
        self.word_regex = re.compile(
            r"[^\s" + re.escape(self.WORD_DELIMITERS) + "]+")

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        for message in self.client.messages:
            content = message.get("content", "")
            if isinstance(content, str):
                found_words = self.word_regex.findall(content)
                words.update(
                    w for w in found_words if os.path.sep not in w and len(w) > 2)
        return words

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        """Gets suggestions from the filesystem using glob."""
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

    def _get_chat_files(self) -> List[str]:
        """Gets all chat files from the chat directory."""
        try:
            chat_files = [
                chat.name for chat in self.client.chat_dir.iterdir()
                if chat.is_file() and chat.suffix == ".json"
            ]
            return sorted(chat_files, reverse=True)
        except OSError:
            return []

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        """Generate completions for the current input."""
        text_before_cursor = document.text_before_cursor

        # --- 1. Special command: 'o ' (load chat) ---
        if text_before_cursor.startswith("o "):
            prefix = text_before_cursor[len("o "):]
            chat_files = self._get_chat_files()
            suggestions = [f for f in chat_files if f.startswith(prefix)]
            for s in suggestions:
                yield Completion(s, start_position=-len(prefix))
            return

        # --- Command '/model ' ---
        if text_before_cursor.startswith("/model "):
            prefix = text_before_cursor[len("/model "):]

            if not self.client.models_config:
                return

            model_keys = self.client.models_config.keys()
            suggestions = [
                name for name in model_keys if name.startswith(prefix)]

            for s in suggestions:
                yield Completion(s, start_position=-len(prefix))
            return

            # Case 2: Completing '--provider' or provider names
            try:
                # Use shlex to handle quoted names
                parts = shlex.split(args_str)
                model_name = parts[0]

                if model_name in self.client.models_config:
                    # If last typed word is --provider, suggest providers
                    if args_str.endswith("--provider "):
                        providers = self.client.models_config[model_name]["providers"].keys(
                        )
                        for p in providers:
                            yield Completion(p, start_position=0)
                        return

                    # If just typed a model, suggest --provider
                    if len(parts) == 1 and args_str.endswith(" "):
                        yield Completion("--provider ", start_position=0)
                        return

            except (ValueError, IndexError):
                pass  # Ignore parsing errors during completion
            return

            model_names = self.client.models_config.keys()
            suggestions = [
                name for name in model_names if name.startswith(prefix)]

            for s in suggestions:
                yield Completion(s, start_position=-len(prefix))
            return

        # --- 2. Special command: '/ global' ---
        elif text_before_cursor.startswith("/ global"):
            script_dir = os.path.dirname(__file__)
            global_commands_dir = os.path.join(script_dir, 'global_commands')

            if text_before_cursor.startswith("/ global/"):
                typed_part = text_before_cursor[len('/ global/'):]
            else:
                typed_part = text_before_cursor[len('/ global'):].lstrip()

            search_prefix = os.path.join(global_commands_dir, typed_part)
            full_path_suggestions = self._get_filesystem_suggestions(
                search_prefix)

            for full_path in full_path_suggestions:
                suggestion = os.path.relpath(
                    full_path, global_commands_dir).replace('\\', '/')
                yield Completion(suggestion, start_position=-len(typed_part))
            return

        # --- 3. General Completion Logic (Fallback) ---
        word_before_cursor = document.get_word_before_cursor(
            pattern=self.word_regex)

        if not word_before_cursor:
            if text_before_cursor.endswith(('/', '\\')):
                prefix = text_before_cursor
            else:
                return
        else:
            prefix = word_before_cursor

        fs_suggestions = self._get_filesystem_suggestions(prefix)

        if os.path.sep not in prefix:
            history_words = self._get_words_from_history()
            history_suggestions = {
                word for word in history_words if word.lower().startswith(prefix.lower())
            }
            all_suggestions = sorted(
                list(history_suggestions.union(set(fs_suggestions))))
        else:
            all_suggestions = sorted(fs_suggestions)

        if len(all_suggestions) == 1 and all_suggestions[0].lower() == prefix.lower():
            suggestions = []
        else:
            suggestions = all_suggestions

        for s in suggestions:
            yield Completion(s, start_position=-len(prefix))
