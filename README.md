# Egg — a tmux‑native multi‑agent chat client (Entropy Gradient)

Egg is a terminal chat app that turns your shell into a comfy control room for LLMs and sub‑agents. It streams beautifully, opens agent panes in tmux, runs local tools (bash/python/JS), and keeps a filesystem ledger of every agent and result.

Highlights
- tmux first: each chat runs in its own tmux session; sub‑agents appear in a right‑hand column of panes
- Live streaming with Markdown rendering, optional “thinking” view, and pretty tool call panels
- Local tools: bash, python, JavaScript-in-browser, search, file editors, and agent tree management
- Easy sub‑agents: /spawn and /spawn_auto (auto tool-approve) with /wait, /tree, and /attach helpers
- Smart completion: models, providers, catalogs (all:provider:model), paths, and recent project words
- Project context: AI.md is auto-injected as “rules”; export full conversation to HTML


## Quick start

Prereqs
- tmux (required)
- Python 3.9+ (rich, prompt_toolkit, requests, tiktoken, tavily, selenium, webdriver_manager)
- A terminal that supports UTF‑8

Install
```bash
# from repo root
python -m venv venv
source venv/bin/activate
pip install rich prompt_toolkit requests tiktoken tavily selenium webdriver-manager

# Add your API keys to .env (see below)
cp .env.example .env  # if you keep one; otherwise create .env
```

Run
```bash
# starts/attaches a tmux session for a fresh agent tree
./chat.sh

# use a specific tree id (attaches/creates):
./chat.sh --tree 1717090000
```
When Egg starts, it prints the session name (egg-tree-<TREE_ID>) and opens the UI in tmux.


## Configure models and keys

Egg reads a single models.json organized by provider. A complete example is included in this repo (see models.json in the project root). It supports:
- multiple providers with independent api_base and api_key_env
- provider-level parameters (e.g., {"cache_prompt": true})
- model-level parameters (override provider), aliases, and max_tokens
- a default_model (optional) used at startup

Environment variables go in .env and are sourced by chat.sh at startup:
- OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY, TOGETHERAI_API_KEY, OPENROUTER_API_KEY, DEEPSEEK_API_KEY, GROQ_API_KEY, BASETEN_API_KEY, LOCAL_API_KEY (as needed)
- DEFAULT_MODEL (optional) — starting model display name or provider:name or alias or all:provider:model
- EG_YES_TOOL_FLAG=1 (optional) — auto-approve tool calls on this agent
- TAVILY_API_KEY (optional) — enables /search

Tip: You can switch models any time with /model (see Commands). Sub‑agents inherit your current selection.


## Key bindings
- Tab — autocomplete; Right Arrow — accept the inline suggestion
- Ctrl+D — submit multiline input
- Ctrl+B — toggle borders on/off
- Ctrl+E — clear current buffer
- Ctrl+C — exit

Multiline input is enabled by default. The prompt shows “You & <model>”.


## Commands you’ll use a lot
General
- /model [name|provider:name|alias|all:prov:model] — switch models or show grouped list if no arg
- /toggleThinkingDisplay — show/hide “Reasoning” stream
- /toggleEscape — show tool call arguments escaped or unescaped in the UI
- /exportHtml [file.html] — export the entire chat as a striking HTML page

Local commands and context
- $ <bash> — run locally and add sanitized output to the chat context
- $$ <bash> — run locally but do NOT add output to context
  • Very long outputs (>800 lines) prompt you to include or truncate. Large results may be saved to .egg/artifacts.

Search and catalogs
- /search <query> — Tavily search (needs TAVILY_API_KEY)
- /updateAllModels <provider> — fetch provider’s full catalog into all-models.json
  • Once fetched, you can choose models with all:provider:model and get autocompletion for them.

Agent orchestration
- /spawn [file.md?] [text] — open a child agent using the given context
- /spawn_auto [file.md?] [text] — same, but auto-approve tool calls (EG_YES_TOOL_FLAG=1)
- /wait <child_id|...|any|all> — wait for specific children; any returns on first completion
- /tree — list children with statuses and return values (pretty and raw views)
- /tree list — list existing trees; /tree use <id> — switch current tree for this UI
- /attach <tree_id> [agent_id] — jump your tmux client to a tree (and optionally a window)
- /o [list|<tree_id>] — attach/switch to a tree’s tmux session from the shell

Housekeeping
- /toggleYesToolFlag — toggle per‑agent auto‑approval for tool calls


## Sub‑agents: how they work
- Spawns open panes in the right column of your current tree’s window.
- The spawned agent receives your selected model unless you override it.
- The initial context is the concatenation of optional file.md contents and extra text. If the path starts with global/, Egg will load it from <repo>/global_commands/.
- Each sub‑agent is instructed to finish with /popContext <return_value>.
- On finish it writes result.json and state.json in .egg/agents/<tree>/<parent>/children/<child_id>/, and notifies the parent; /wait will pick it up.

Filesystem layout (per tree)
```
.egg/agents/<TREE_ID>/
  root/
    children/
      label-001/
        state.json
        result.json (when done)
        init_context.txt
        messages.json (seed)
```


## Streaming, display, and Markdown
- Rich Markdown rendering is used when Egg detects Markdown-like content.
- Tool calls are shown as prettified panels; code‑ish bodies are syntax highlighted.
- In tmux, deltas stream in a pane; upon completion Egg also prints a pretty, static view.


## Tools available to the model
These are exposed as OpenAI‑function style tools to your provider. Egg can also parse structured tool calls out of the assistant’s text.

- bash {script}
- python {script}
- javascript {script, url?}
  • Requires Chrome/Chromium launched with --remote-debugging-port=9222
  • Egg attaches to an existing tab that matches the URL (exact match or exact query params), or opens a new one
- search {query} — Tavily
- str_replace_editor {file_path, old_str, new_str}
- replace_lines {file_path, start_line, end_line?, new_content, action, position}
- spawn_agent {context_text, label?, model_key?}
- spawn_agent_auto {context_text, label?, model_key?}
- wait_agents {which: [...], timeout_sec?, any_mode?}
- popContext {return_value}

Confirmation flow
- By default Egg asks “Execute the <tool> call(s)? [y/n/a]” and supports approving all calls for one assistant turn.
- Set EG_YES_TOOL_FLAG=1 (or use /toggleYesToolFlag) to auto‑approve for this agent.


## Project context and saving
- If an AI.md exists in your project root, Egg appends its contents to the system prompt under “THIS PROJECT’S INSTRUCTIONS AND RULES”.
- Conversations are saved in .egg/localChats/ as JSON, with the active model recorded per message.
- Sub‑agents persist their selection and tmux pane in their own state.json for deterministic pane targeting.

Export to HTML
- /exportHtml my-chat.html — produces a dark, readable, single‑file HTML with panels and code blocks.


## Models: switching and catalogs
- /model with no arguments prints models grouped by provider (from models.json).
- You can select by:
  • exact display name (e.g., OpenAI GPT-4o)
  • provider:name (e.g., openai:gpt-4o)
  • alias (if assigned)
  • all:provider:model (provider catalogs after /updateAllModels provider)
- The completer suggests all of the above and can surface catalog models even when you type fragments like "llama".


## Local commands with context ($ and $$)
- $ ls -la — executes locally and adds a sanitized preview of output to the transcript in fenced blocks.
- $$ df -h — executes locally but does not add to the transcript.
- Long outputs (>800 lines) trigger a prompt to include or not; very large outputs are truncated for the model and saved to .egg/artifacts/.


## Troubleshooting
- tmux: Make sure tmux is installed. Egg creates sessions named egg-tree-<TREE_ID>.
- 401/403 or “Authorization NOT_SET”: Add the right API key to .env for the provider you selected.
- Chrome JS tool: Launch Chrome/Chromium with remote debugging enabled:
  ```bash
  chromium --remote-debugging-port=9222 &
  # or
  /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222 &
  ```
- Tavily search: set TAVILY_API_KEY in .env.
- Large outputs: use $$ to avoid polluting context, or answer “n” when asked to include full output.


## Notes for power users
- Trees: /tree list and /tree use let you hop between agent trees from the same UI.
- Attach: /attach <tree> [agent_id] jumps to the tmux session/window; /o list shows available trees.
- State propagation: The currently selected model is persisted so children inherit it even across new panes.
- Safety: bash tool has a 60s timeout; file editors guard against editing system directories.


Made with care by Entropy Gradient. Have fun hatching agents.
