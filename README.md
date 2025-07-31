# Egg: An Extensible AI Chat Terminal

Egg is a powerful, terminal-based chat application designed for developers and power users. It supports multiple AI models, providers, tool use, and a range of quality-of-life features to make interacting with AI assistants fast and efficient.


![Egg](egg.gif)


## Features

- Multi-Model & Multi-Provider: Seamlessly switch between different AI models from various providers (e.g., OpenAI, Anthropic, Google, local instances) right in the middle of a chat.
- Persistent Chat History: Your conversation context is preserved when you switch models.
- Streaming Responses: Responses from the assistant, including tool calls and reasoning, are streamed live for a responsive feel.
- Interactive Tool Use: The assistant can request to execute `bash` and `python` scripts, which you can approve or deny before they run.
- Configuration-Driven:
  - models.json: Define the models you want to use with a unique display name for each model-provider pair.
  - providers.json: Configure the API endpoints and environment variables for each provider's API key.
    - Here you define the names of the variables that will hold the API key
    - chat.sh automatically sources .env in the project root, so you can put API keys there (e.g., `export OPENAI_API_KEY="sk-..."`).
- Rich Terminal UI:
  - Built with `rich` and `prompt_toolkit`.
  - Autocompletion for commands and file paths.
  - Optional UI borders for a cleaner look.
- Shortcuts for Efficiency:
  - Tab: Autocomplete commands and paths.
  - Right Arrow: Accept autocompletion suggestions.
  - Ctrl+D: Submit your message.
  - Ctrl+C: Exit the application or interrupt the AI's response.
  - Ctrl+B: Toggle UI borders on/off.
  - Ctrl+E: Clear the current input.
- Context and Subagents:
  - Manage conversation context with `/pushContext` and `/popContext`.
  - Spawn subagents with `/spawn`. Subagents run in panes within the same window as the parent:
    - First child: vertical split (right column).
    - Siblings: horizontal splits stacked inside the right column.
    - Applies recursively for deeper layers.

## Setup

1) Dependencies
```bash
pip install -r requirements.txt
```

2) Providers Configuration (providers.json)
This file maps a provider name to its API endpoint and the environment variable that holds its API key.

Example providers.json:
```json
{
  "openai": {"api_base": "https://api.openai.com/v1/chat/completions", "api_key_env": "OPENAI_API_KEY"},
  "local":  {"api_base": "http://localhost:11434/v1/chat/completions", "api_key_env": "LOCAL_API_KEY"}
}
```

3) Models Configuration (models.json)
This file defines the models you want to use. The key is a unique, human-friendly display name.

Example models.json:
```json
{
  "OpenAI GPT-4o": {"provider": "openai", "model_name": "gpt-4o", "max_tokens": 128000},
  "Local Llama3":  {"provider": "local",  "model_name": "llama3", "max_tokens": 8000}
}
```

4) API Keys
```bash
export OPENAI_API_KEY="sk-..."
# or set them in .env which chat.sh sources automatically
```

## Usage

Run the application with:
```bash
./chat.sh
```

### Core Commands

- /model <display_name>
  Switch the active AI model. Typing `/model ` and pressing Tab lists available models.

- Context commands
  - /pushContext <message_or_filepath_and_message>
    Start a new context from text or a .md file (local or `global/`). Include instructions that the subagent should `/popContext` when done.
  - /popContext <return_value>
    Return to the previous context and append the return value into the parent context.

- /spawn [<file.md>] [<additional text>]
  Spawn a subagent. Layout rule per parent:
  - First child: split parent’s pane vertically → creates a right column.
  - Subsequent children: split horizontally inside that right column (stack).
  - Recurses for deeper layers.

- /wait <id...> | any | all
  Wait for child agents.
  - any: return as soon as one child completes (even if it finished earlier). The completed child’s pane is closed.
  - all: wait for all current children (or specified ids). All their panes are closed after results are received.
  - explicit ids: wait for exactly those children.

- /toggleYesToolFlag
  Toggle whether tool calls (bash/python) auto-execute without confirmation.

- /toggleThinkingDisplay
  Toggle live display of the AI’s reasoning panel.

- b <command>
  Execute a local bash command and inject the output as context. Example: `b ls -l`.

### Trees and tmux
- Each run creates a new agent tree (unless EG_TREE_ID is preset or you switch with `/tree use`).
- /o and /o list
  - Show available trees or attach to a tree’s tmux session.
- /tree
  - /tree list shows existing trees and marks the current.
  - /tree use <TREE_ID> switches the active tree for this session.
- /attach <TREE_ID> [agent_id]
  - Attach to a tree’s tmux session (optionally focus agent window/pane).

### Notes on layout and panes
- Subagents are panes within the same window as the parent.
- First child gets the right column by vertical split; siblings stack in that column via horizontal splits.
- Panes are targeted by pane id (TMUX_PANE), which is recorded at agent startup for deterministic behavior.
- When children finish, parents clean up their panes on /wait completion.

### Keyboard Shortcuts

| Shortcut  | Action                              |
|-----------|-------------------------------------|
| Tab       | Autocomplete commands and file paths|
| Right Arrow | Accept autocompletion suggestion  |
| Ctrl+D    | Submit message                      |
| Ctrl+C    | Exit application or interrupt reply |
| Ctrl+B    | Toggle UI borders on/off            |
| Ctrl+E    | Clear current input                 |

## Configuration Files

- AI.md: Project-specific directives for Egg.
- systemPrompt: Base system prompt sent to all models.
- models.json: Model definitions and configs.
- providers.json: API endpoint and key variable mapping.

## Testing

See the full end-to-end test plan: [EndToEnd.md](EndToEnd.md)

## Development

The application is built with Python 3.10+ and uses:
- prompt_toolkit for terminal UI and autocompletion
- rich for rich text formatting
- requests for HTTP API calls
- JSON for configuration

## Troubleshooting

- Ensure required environment variables are set.
- Validate that `models.json` and `providers.json` are proper JSON.
- Verify API endpoints are accessible.
- Use `b ls .egg/localChats/` to list saved chats.
- If pane layout looks off after closing children, try `/wait all` to clean up panes and spawn again.
