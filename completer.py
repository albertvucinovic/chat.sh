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
        # The regex now correctly uses the WORD_DELIMITERS constant
        self.word_regex = re.compile(r"[^\s" + re.escape(self.WORD_DELIMITERS) + "]+")

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        for message in self.client.messages:
            content = message.get("content", "")
            if isinstance(content, str):
                found_words = self.word_regex.findall(content)
                words.update(w for w in found_words if os.path.sep not in w and len(w) > 2)
        return words

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        """Gets suggestions from the filesystem using glob."""
        try:
            # Expand user tilde for paths like ~/
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
        text = document.text_before_cursor
        
        # This is the corrected line
        word_before_cursor = document.get_word_before_cursor(pattern=self.word_regex)

        suggestions = []

        # Handle "o " command for chat file completion
        if text.startswith("o "):
            command_prefix = text[len("o "):]
            chat_files = self._get_chat_files()
            suggestions = [f for f in chat_files if f.startswith(command_prefix)]
            word_before_cursor = command_prefix
        else:
            # Only trigger completion if there's a word to complete or we're at a path separator
            if not word_before_cursor and not text.endswith(('/', '\\')):
                return

            fs_suggestions = self._get_filesystem_suggestions(text if text.endswith(('/', '\\')) else word_before_cursor)
            
            if os.path.sep not in word_before_cursor:
                history_words = self._get_words_from_history()
                history_suggestions = {
                    word for word in history_words if word.lower().startswith(word_before_cursor.lower())
                }
                all_suggestions = sorted(list(history_suggestions.union(set(fs_suggestions))))
            else:
                all_suggestions = sorted(fs_suggestions)

            if len(all_suggestions) == 1 and all_suggestions[0].lower() == word_before_cursor.lower():
                 suggestions = []
            else:
                 suggestions = all_suggestions

        # Yield Completion objects for prompt-toolkit
        for s in suggestions:
            yield Completion(s, start_position=-len(word_before_cursor))
