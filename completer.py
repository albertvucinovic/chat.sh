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
            "/model", "/pushContext", "/popContext", "/toggleYesToolFlag", "/toggleThinkingDisplay", "o", "b", "/replace_lines", "/spawn", "/wait", "/tree", "/attach"
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
                local_chats_dir = Path.cwd() / ".egg/localChats"
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

        # Handler for: /pushContext [<file_path.md>] [<additional_text>]
        elif text.startswith("/pushContext"):
            # Get the input after the command and any leading spaces
            input_after_command = text[len("/pushContext"):].lstrip()
            current_fragment = document.get_word_before_cursor(WORD=True)
            
            # Regex to check if a file path (ending in .md) has been typed and is followed by a space
            # This helps determine if we are now in the 'additional_text' part.
            file_followed_by_space_match = re.match(r"^\S+\.md\s.*", input_after_command)
            
            # If an .md file path was recognized and followed by a space, we are in the additional_text part.
            # OR, if there's no fragment currently being typed, but there's content after the command
            # AND it doesn't look like a file path being typed (e.g., "/pushContext some text"),
            # then also treat it as additional text.
            is_in_additional_text_mode = False
            if file_followed_by_space_match: # Case: /pushContext file.md <cursor_here_or_after>
                is_in_additional_text_mode = True
            elif ' ' in input_after_command and not re.match(r"^\S+\.md", input_after_command.split(' ')[0]):
                # Case: /pushContext some other text (no .md file, but multiple words)
                is_in_additional_text_mode = True
            elif not current_fragment and input_after_command.strip() and not file_followed_by_space_match: # Case: /pushContext <cursor after command>
                # If cursor is after command, and there's content but no fragment, and it's not a file followed by space
                # This means it's the beginning of additional text or a file not yet typed.
                # We will prioritize file suggestions first below.
                pass # Let it fall through to file/general word suggestions
            
            if is_in_additional_text_mode or (not current_fragment and input_after_command.strip() and not file_followed_by_space_match):
                # Provide general word completion (recent words, AI.md words)
                if current_fragment or input_after_command.endswith(' '): # Only suggest if typing something or just typed a space
                    recent_words = self.client.get_recent_words_for_completion(limit=200)
                    aimd_words = self.client.get_aimd_words_for_completion()
                    all_words = aimd_words + recent_words
                    seen = set()
                    matches = [w for w in all_words if w.lower().startswith(current_fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                    for w in matches:
                        yield Completion(w, start_position=-len(current_fragment))
                return
            
            # If not in additional text mode, it means we are potentially typing the file path
            # or the very first word of the additional text without a preceding .md file.
            # Prioritize file path suggestions for the current fragment.
            
            # Handle 'global/' prefix specifically
            if 'global/'.startswith(current_fragment):
                yield Completion('global/', start_position=-len(current_fragment))

            if current_fragment.startswith('global/'):
                path_part = current_fragment[len('global/'):]
                script_dir = os.path.dirname(os.path.realpath(__file__))
                global_dir = os.path.join(script_dir, 'global_commands')
                search_path = os.path.join(global_dir, path_part)
                
                suggestions = self._get_filesystem_suggestions(search_path)
                for s in suggestions:
                    rel_path = 'global/' + os.path.relpath(s, global_dir).replace('\\', '/')
                    yield Completion(rel_path, start_position=-len(current_fragment))
                if suggestions: return # If global file suggestions are found, stop here
            else:
                # Try local file suggestions
                suggestions = self._get_filesystem_suggestions(current_fragment)
                for s in suggestions:
                    yield Completion(s, start_position=-len(current_fragment))
                if suggestions: return # If local file suggestions are found, stop here
            
            # If no file suggestions were found (or applicable for the current fragment),
            # then fall back to general word completion. This covers cases where the user
            # starts typing additional text without an .md file, or just hits space after /pushContext
            if current_fragment or input_after_command.endswith(' '):
                recent_words = self.client.get_recent_words_for_completion(limit=200)
                aimd_words = self.client.get_aimd_words_for_completion()
                all_words = aimd_words + recent_words
                seen = set()
                matches = [w for w in all_words if w.lower().startswith(current_fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                for w in matches:
                    yield Completion(w, start_position=-len(current_fragment))
            return

        # Spawn mirrors /pushContext suggestions for file path and words
        elif text.startswith("/spawn"):
            input_after_command = text[len("/spawn"):].lstrip()
            current_fragment = document.get_word_before_cursor(WORD=True)
            file_followed_by_space_match = re.match(r"^\S+\.md\s.*", input_after_command)
            is_in_additional_text_mode = False
            if file_followed_by_space_match:
                is_in_additional_text_mode = True
            elif ' ' in input_after_command and not re.match(r"^\S+\.md", input_after_command.split(' ')[0]):
                is_in_additional_text_mode = True
            elif not current_fragment and input_after_command.strip() and not file_followed_by_space_match:
                pass

            if is_in_additional_text_mode or (not current_fragment and input_after_command.strip() and not file_followed_by_space_match):
                if current_fragment or input_after_command.endswith(' '):
                    recent_words = self.client.get_recent_words_for_completion(limit=200)
                    aimd_words = self.client.get_aimd_words_for_completion()
                    all_words = aimd_words + recent_words
                    seen = set()
                    matches = [w for w in all_words if w.lower().startswith(current_fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                    for w in matches:
                        yield Completion(w, start_position=-len(current_fragment))
                return

            if 'global/'.startswith(current_fragment):
                yield Completion('global/', start_position=-len(current_fragment))

            if current_fragment.startswith('global/'):
                path_part = current_fragment[len('global/'):]
                script_dir = os.path.dirname(os.path.realpath(__file__))
                global_dir = os.path.join(script_dir, 'global_commands')
                search_path = os.path.join(global_dir, path_part)
                suggestions = self._get_filesystem_suggestions(search_path)
                for s in suggestions:
                    rel_path = 'global/' + os.path.relpath(s, global_dir).replace('\\', '/')
                    yield Completion(rel_path, start_position=-len(current_fragment))
                if suggestions: return
            else:
                suggestions = self._get_filesystem_suggestions(current_fragment)
                for s in suggestions:
                    yield Completion(s, start_position=-len(current_fragment))
                if suggestions: return

            if current_fragment or input_after_command.endswith(' '):
                recent_words = self.client.get_recent_words_for_completion(limit=200)
                aimd_words = self.client.get_aimd_words_for_completion()
                all_words = aimd_words + recent_words
                seen = set()
                matches = [w for w in all_words if w.lower().startswith(current_fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                for w in matches:
                    yield Completion(w, start_position=-len(current_fragment))
            return

        elif text.startswith("/popContext "):
            return

        elif text.startswith("/tree "):
            # Suggest existing tree ids from filesystem
            try:
                base = Path('.egg/agents')
                if base.is_dir():
                    prefix = text[len('/tree '):]
                    for d in base.iterdir():
                        if d.is_dir() and d.name.startswith(prefix):
                            yield Completion(d.name, start_position=-len(prefix))
            except Exception:
                pass
            return

        elif text.startswith("/attach"):
            parts = text.split()
            if len(parts) == 1:
                # Suggest tree ids
                try:
                    base = Path('.egg/agents')
                    if base.is_dir():
                        for d in base.iterdir():
                            if d.is_dir():
                                yield Completion(d.name, start_position=0)
                except Exception:
                    pass
                return
            elif len(parts) == 2 and not text.endswith(' '):
                # Complete tree id
                prefix = parts[1]
                try:
                    base = Path('.egg/agents')
                    for d in base.iterdir():
                        if d.is_dir() and d.name.startswith(prefix):
                            yield Completion(d.name, start_position=-len(prefix))
                except Exception:
                    pass
                return
            else:
                # Suggest child ids under the given tree
                tree_id = parts[1] if len(parts) > 1 else 'default'
                prefix = parts[2] if len(parts) > 2 else ''
                try:
                    child_root = Path('.egg/agents')/tree_id/'root'/'children'
                    if child_root.is_dir():
                        for d in child_root.iterdir():
                            if d.is_dir() and d.name.startswith(prefix):
                                yield Completion(d.name, start_position=-len(prefix))
                except Exception:
                    pass
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
