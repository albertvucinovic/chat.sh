import os
import re
from typing import List, Set, Optional

class Completer:
    """
    Manages completion state and suggestion generation from history and
    the filesystem.
    """

    def __init__(self, client: "ChatClient"):
        self.client = client
        self.suggestions: List[str] = []
        self.current_index = -1
        self.active = False

    def _get_words_from_history(self) -> Set[str]:
        """Extracts all unique words from the message history."""
        words = set()
        word_regex = re.compile(r"[\w.-]+")
        for message in self.client.messages:
            content = message.get("content", "")
            found_words = word_regex.findall(content.lower())
            words.update(found_words)
        return words

    def _get_words_from_filesystem(self) -> Set[str]:
        """Gets all file and directory names from the current directory."""
        try:
            return set(os.listdir("."))
        except OSError:
            return set()

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

    def find_suggestions(self, line: List[str]):
        """
        Generate suggestions based on the word before the cursor.
        The "word" is defined as everything after the last whitespace or delimiter.
        """
        current_text = "".join(line)
        delimiters = " `~!@#$%^&*()=+[{]}\\|;:'\",<>/?"
        word_start_index = 0
        for i in range(len(current_text) - 1, -1, -1):
            if current_text[i] in delimiters:
                word_start_index = i + 1
                break

        prefix = current_text[word_start_index:]

        # Handle "o " command for chat file completion
        if current_text.startswith("o "):
            chat_files = self._get_chat_files()
            if not prefix:  # Show all chat files when no prefix is provided
                self.suggestions = sorted(chat_files, reverse=True)
            else:
                self.suggestions = sorted(
                    [
                        chat_file
                        for chat_file in chat_files
                        if chat_file.lower().startswith(prefix.lower())
                    ],
                    reverse=True
                )
        else:
            if not prefix:
                self.reset()
                return

            history_words = self._get_words_from_history()
            fs_words = self._get_words_from_filesystem()
            all_words = history_words.union(fs_words)

            self.suggestions = sorted(
                [
                    word
                    for word in all_words
                    if word.lower().startswith(prefix.lower())
                    and word.lower() != prefix.lower()
                ]
            )

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
        delimiters = " \t\n`~!@#$%^&*()=+[{]}\\|;:'\",<>/?"
        word_start_index = 0
        for i in range(len(current_text) - 1, -1, -1):
            if current_text[i] in delimiters:
                word_start_index = i + 1
                break

        if os.path.isdir(suggestion):
            suggestion += "/"

        new_line = list(current_text[:word_start_index])
        new_line.extend(list(suggestion))
        return new_line

    def reset(self):
        """Resets the completer state."""
        self.suggestions = []
        self.current_index = -1
        self.active = False
