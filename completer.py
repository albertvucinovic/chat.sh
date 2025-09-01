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
            "/model", "/popContext", "/toggleYesToolFlag", "/toggleThinkingDisplay", "/o", "/spawn", "/spawn_auto", "/wait", "/tree", "/attach", "/updateAllModels", "/search", "/toggleEscape", "/exportHtml", "/drop"
        ]

    def _get_filesystem_suggestions(self, prefix: str) -> List[str]:
        """Provides filesystem suggestions for a given prefix, handling '~'."""
        try:
            expanded_prefix = os.path.expanduser(prefix)
            escaped_prefix = glob.escape(expanded_prefix)
            matches = glob.glob(escaped_prefix + '*')
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

    def _model_suggestions(self, prefix: str):
        """Suggest models grouped by provider, with support for provider:name and aliases, plus 'all:' catalogs.
        Matches the user's input anywhere in the candidate (not just prefix). Normalizes punctuation and case so
        middle-part hints like "gpt 3" will match "OpenAI GPT-3 OR". Also applies substring matching for all: catalogs
        even when the user doesn't type the all: prefix (so 'llama' can match 'all:togetherai:meta-llama/...').
        """
        def _normalize(s: str) -> str:
            if not s:
                return ""
            ns = re.sub(r"[^0-9a-z]+", " ", s.lower()).strip()
            ns = re.sub(r"\s+", " ", ns)
            return ns

        seen = set()
        pref_norm = _normalize(prefix)

        # If user is explicitly using all: handle provider or model completion
        if prefix.lower().startswith('all:'):
            rest = prefix[4:]
            # No provider yet: delegate to helper to suggest providers
            if ':' not in rest:
                for s in self.client.get_all_models_suggestions(prefix):
                    yield Completion(s, start_position=-len(prefix))
                return
            prov, partial = rest.split(':', 1)
            mids = self.client.get_all_models_for_provider(prov) or []
            part_norm = _normalize(partial)
            for mid in mids:
                cand = f"all:{prov}:{mid}"
                if part_norm == "" or part_norm in _normalize(mid) or part_norm in _normalize(cand):
                    if cand not in seen:
                        seen.add(cand)
                        yield Completion(cand, start_position=-len(prefix))
            return

        # Match standard configured models (models.json)
        display_names = list(self.client.models_config.keys()) if getattr(self.client, 'models_config', None) else []
        for name in sorted(display_names):
            if pref_norm == "" or pref_norm in _normalize(name):
                if name not in seen:
                    seen.add(name)
                    yield Completion(name, start_position=-len(prefix))

        # provider:name and provider:alias forms
        for display, cfg in (self.client.models_config or {}).items():
            prov = cfg.get('provider', 'unknown')
            prov_pref = f"{prov}:{display}"
            if pref_norm == "" or pref_norm in _normalize(prov_pref):
                if prov_pref not in seen:
                    seen.add(prov_pref)
                    yield Completion(prov_pref, start_position=-len(prefix))
            for a in cfg.get('alias', []) or []:
                if not isinstance(a, str):
                    continue
                prov_alias = f"{prov}:{a}"
                if pref_norm == "" or pref_norm in _normalize(prov_alias):
                    if prov_alias not in seen:
                        seen.add(prov_alias)
                        yield Completion(prov_alias, start_position=-len(prefix))

        # plain aliases
        for display, cfg in (self.client.models_config or {}).items():
            for a in cfg.get('alias', []) or []:
                if isinstance(a, str) and (pref_norm == "" or pref_norm in _normalize(a)):
                    if a not in seen:
                        seen.add(a)
                        yield Completion(a, start_position=-len(prefix))

        # Additionally, search cached provider-wide catalogs (all-models cache) so simple fragments like
        # 'llama' will surface provider-specific model ids as 'all:provider:model-id'
        if pref_norm:
            try:
                providers = self.client.get_providers() or []
                for prov in providers:
                    mids = self.client.get_all_models_for_provider(prov) or []
                    for mid in mids:
                        cand = f"all:{prov}:{mid}"
                        if cand in seen:
                            continue
                        # Match against the model id and a more humanized form (split slashes and dashes)
                        if pref_norm in _normalize(mid) or pref_norm in _normalize(cand):
                            seen.add(cand)
                            yield Completion(cand, start_position=-len(prefix))
            except Exception:
                pass

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        words = text.split(' ')

        if text.startswith("/o"):
            parts = text.split()
            base = Path('.egg/agents')
            try:
                trees = []
                if base.is_dir():
                    trees = [d.name for d in base.iterdir() if d.is_dir() and d.name != '.current_tree']
            except Exception:
                trees = []

            if text == "/o" or text == "/o ":
                yield Completion("list", start_position=0)
                for t in sorted(trees):
                    yield Completion(t, start_position=0)
                return

            if text.startswith("/o list"):
                return

            if text.startswith("/o "):
                prefix = text[len("/o "):]
                for t in sorted(trees):
                    if t.startswith(prefix):
                        yield Completion(t, start_position=-len(prefix))
                return

        elif text.startswith("/model "):
            prefix = text[len("/model "):]
            # Provide rich suggestions: display, provider:name, aliases, and all: catalogs
            for c in self._model_suggestions(prefix):
                yield c
            return

        elif text.startswith("/updateAllModels "):
            # Suggest providers
            prefix = text[len("/updateAllModels "):]
            try:
                for prov in sorted(self.client.get_providers()):
                    if prov.startswith(prefix):
                        yield Completion(prov, start_position=-len(prefix))
            except Exception:
                pass
            return

        elif text.startswith("/spawn_auto"):
            input_after_command = text[len("/spawn_auto"):].lstrip()
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
                path_part = current_fragment[len('global/') :]
                script_dir = os.path.dirname(os.path.realpath(__file__))
                global_dir = os.path.join(script_dir, 'global_commands')
                search_path = os.path.join(global_dir, path_part)
                suggestions = self._get_filesystem_suggestions(search_path)
                for s in suggestions:
                    rel_path = 'global/' + os.path.relpath(s, global_dir).replace('\\', '/')
                    yield Completion(rel_path, start_position=-len(current_fragment))
                if suggestions:
                    return
            else:
                suggestions = self._get_filesystem_suggestions(current_fragment)
                for s in suggestions:
                    yield Completion(s, start_position=-len(current_fragment))
                if suggestions:
                    return

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

        elif text.startswith("/toggleEscape"):
            return

        elif text.startswith("/exportHtml "):
            # Provide filesystem suggestions for the HTML export filename
            prefix = text[len("/exportHtml "):]
            suggestions = self._get_filesystem_suggestions(prefix)
            for s in suggestions:
                yield Completion(s, start_position=-len(prefix))
            return

        elif text.startswith("/toggleYesToolFlag"):
            return

        elif text.startswith("/toggleThinkingDisplay"):
            return

        elif text.startswith("/drop"):
            return

        elif text.startswith("/tree use "):
            prefix = text[len('/tree use '):]
            try:
                base = Path('.egg/agents')
                if base.is_dir():
                    for d in base.iterdir():
                        if d.is_dir() and d.name != '.current_tree' and d.name.startswith(prefix):
                            yield Completion(d.name, start_position=-len(prefix))
            except Exception:
                pass
            return

        elif text.startswith("/tree "):
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

        elif len(words) == 1 and not text.endswith(' '):
            prefix = words[0]
            for cmd in self.all_commands:
                if cmd.startswith(prefix):
                    yield Completion(cmd, start_position=-len(prefix))
            return

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

        if not text.strip().startswith(('/', '/o ', 'b ', '/model ', '/popContext ', '/updateAllModels ')):
            line = document.text_before_cursor
            m = re.search(r'(\w{3,})$', line)
            if m:
                fragment = m.group(1)
                recent_words = self.client.get_recent_words_for_completion(limit=200)
                aimd_words = self.client.get_aimd_words_for_completion()

                all_words = aimd_words + recent_words

                seen = set()
                matches = [w for w in all_words if w.lower().startswith(fragment.lower()) and not (w.lower() in seen or seen.add(w.lower()))]
                for w in matches:
                    yield Completion(w, start_position=-len(fragment))
            return
