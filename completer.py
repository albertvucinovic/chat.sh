from pathlib import Path
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
        self.all_commands = [
            "/model", "/pushContext", "/popContext", "/toggleYesToolFlag", "/toggleThinkingDisplay", "o", "b"
        ]

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
        words = text.split(' ')

        # Handler for: o <chat_file>
        if text.startswith("o "):
            prefix = text[len("o "):]
            suggestions = set()
            try:
                local_chats_dir = Path.cwd() / "localChats"
                if local_chats_dir.is_dir():
                    chat_files = [f.name for f in local_chats_dir.iterdir()
                                  if f.name.startswith(prefix) and f.suffix == ".json"]
                    for f_name in chat_files:
                        suggestions.add(f_name)
            except OSError:
                pass

            for s in sorted(list(suggestions), reverse=True):
                yield Completion(s, start_position=-len(prefix))
            return

        # Handler for: /model <model_key>
        elif text.startswith("/model "):
            prefix = text[len("/model "):]
            if self.client.models_config:
                for name in self.client.models_config.keys():
                    if name.startswith(prefix):
                        yield Completion(name, start_position=-len(prefix))
            return

        # Handler for: /pushContext <context_or_file>
        elif text.startswith("/pushContext "):
            prefix = text[len("/pushContext "):]

            if 'global/'.startswith(prefix):
                yield Completion('global/', start_position=-len(prefix))

            if prefix.startswith('global/'):
                path_part = prefix[len('global/'):]
                script_dir = os.path.dirname(os.path.realpath(__file__))
                global_dir = os.path.join(script_dir, 'global_commands')
                search_path = os.path.join(global_dir, path_part)
                
                suggestions = self._get_filesystem_suggestions(search_path)
                for s in suggestions:
                    rel_path = 'global/' + os.path.relpath(s, global_dir).replace('\\', '/')
                    yield Completion(rel_path, start_position=-len(prefix))
            else:
                suggestions = self._get_filesystem_suggestions(prefix)
                for s in suggestions:
                    yield Completion(s, start_position=-len(prefix))
            return

        # Handler for: /popContext <return_value>
        elif text.startswith("/popContext "):
            return

        # Handler for command names themselves
        elif len(words) == 1 and not text.endswith(' '):
            prefix = words[0]
            for cmd in self.all_commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

        # --- General Fallback Logic for Filesystem Paths ---
        else:
            parts = text.split()
            if not parts or text.endswith(' '):
                return

            prefix_to_complete = parts[-1]
            suggestions = self._get_filesystem_suggestions(prefix_to_complete)

            if len(suggestions) == 1 and suggestions[0].lower() == prefix_to_complete.lower():
                return

            for s in suggestions:
                yield Completion(s, start_position=-len(prefix_to_complete))
            if suggestions:
                return

        # --- Word completion from history for freeform chat ---
        if not text.strip().startswith(('/', 'o ', 'b ', '/model ', '/pushContext ', '/popContext ')):
            line = document.text_before_cursor
            m = re.search(r'(\w{3,})$', line)
            if m:
                fragment = m.group(1)
                recent_words = self.client.get_recent_words_for_completion(limit=200)
                aimd_words = self.client.get_aimd_words_for_completion()

                # Combine words, with AI.md words taking precedence, then recent words.
                all_words = aimd_words + recent_words

                seen = set()
                matches = [w for w in all_words if w.lower().startswith(fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                for w in matches:
                    yield Completion(w, start_position=-len(fragment))
            return
