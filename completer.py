import os
import re
import glob
from typing import List, Set, Optional

class Completer:
    """
    Manages completion state and suggestion generation from history and
    the filesystem.
    """
    # Define delimiters that separate "words", excluding path separators.
    WORD_DELIMITERS = " `~!@#$%^&*()=+[{]}|;:'\",<>"

    def __init__(self, client: "ChatClient"):
        self.client = client
        self.suggestions: List[str] = []
        self.current_index = -1
        self.active = False

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        # This regex can be simpler as we handle delimiters separately
        word_regex = re.compile(r"[^\s" + re.escape(self.WORD_DELIMITERS) + "]+")
        for message in self.client.messages:
            content = message.get("content", "")
            found_words = word_regex.findall(content)
            words.update(w for w in found_words if os.path.sep not in w)
        return words

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        """
        Gets suggestions from the filesystem using glob, handling subdirectories.
        Appends a path separator to directories.
        """
        try:
            # Use glob to find all matching paths. The '*' handles completion.
            matches = glob.glob(prefix + '*')
            
            suggestions = []
            for match in matches:
                # Normalize path separators for consistency
                normalized_match = match.replace('\\', '/')
                if os.path.isdir(normalized_match):
                    suggestions.append(normalized_match + '/')
                else:
                    suggestions.append(normalized_match)
            return suggestions
        except (OSError, PermissionError):
            return []

    def _get_chat_files(self) -> List[str]:
        """Gets all chat files from the chat directory, sorted by time descending."""
        try:
            chat_files = [
                str(chat.name)
                for chat in self.client.chat_dir.iterdir()
                if chat.is_file() and chat.suffix == ".json"
            ]
            chat_files.sort(reverse=True)
            return chat_files
        except OSError:
            return []

    def _get_current_word_prefix(self, text: str) -> (str, int):
        """Finds the word prefix to be completed and its start index."""
        word_start_index = 0
        for i in range(len(text) - 1, -1, -1):
            if text[i] in self.WORD_DELIMITERS:
                word_start_index = i + 1
                break
        return text[word_start_index:], word_start_index

    def find_suggestions(self, line: List[str]):
        """
        Generate suggestions based on the word before the cursor.
        """
        current_text = "".join(line)
        prefix, _ = self._get_current_word_prefix(current_text)

        # Handle "o " command for chat file completion
        if current_text.startswith("o "):
            chat_files = self._get_chat_files()
            # The prefix for 'o' command is the part after 'o '
            command_prefix = current_text[len("o "):]
            if not command_prefix:
                self.suggestions = sorted(chat_files, reverse=True)
            else:
                self.suggestions = sorted(
                    [
                        chat_file
                        for chat_file in chat_files
                        if chat_file.lower().startswith(command_prefix.lower())
                    ]
                )
        else:
            if not prefix:
                self.reset()
                return

            fs_suggestions = self._get_filesystem_suggestions(prefix)
            
            # Only add history words if we are not in the middle of a path
            if os.path.sep not in prefix:
                history_words = self._get_words_from_history()
                history_suggestions = {
                    word
                    for word in history_words
                    if word.lower().startswith(prefix.lower())
                }
                # Combine, deduplicate, and sort
                all_suggestions = sorted(list(history_suggestions.union(set(fs_suggestions))))
            else:
                all_suggestions = sorted(fs_suggestions)

            # Filter out the exact prefix if it's the only suggestion
            if len(all_suggestions) == 1 and all_suggestions[0].lower() == prefix.lower():
                 self.suggestions = []
            else:
                 self.suggestions = all_suggestions


        if self.suggestions:
            self.active = True
            self.current_index = -1
        else:
            self.reset()

    def next_suggestion(self) -> Optional[str]:
        """Cycles to the next suggestion."""
        if not self.suggestions:
            return None
        self.current_index = (self.current_index + 1) % len(self.suggestions)
        return self.suggestions[self.current_index]

    def previous_suggestion(self) -> Optional[str]:
        """Cycles to the previous suggestion."""
        if not self.suggestions:
            return None
        self.current_index = (self.current_index - 1 + len(self.suggestions)) % len(
            self.suggestions
        )
        return self.suggestions[self.current_index]

    def apply_suggestion(self, current_line: List[str], suggestion: str) -> List[str]:
        """Replaces the current word with the chosen suggestion."""
        current_text = "".join(current_line)
        _, word_start_index = self._get_current_word_prefix(current_text)

        new_line = list(current_text[:word_start_index])
        new_line.extend(list(suggestion))
        return new_line

    def reset(self):
        """Resets the completer state."""
        self.suggestions = []
        self.current_index = -1
        self.active = False
