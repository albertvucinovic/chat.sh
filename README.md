# Egg: An Extensible AI Chat Terminal

Egg is a powerful, terminal-based chat application designed for developers and power users. It supports multiple AI models, providers, tool use, and a range of quality-of-life features to make interacting with AI assistants fast and efficient.


![Egg](egg.gif)


## Features

- **Multi-Model & Multi-Provider**: Seamlessly switch between different AI models from various providers (e.g., OpenAI, Anthropic, Google, local instances) right in the middle of a chat.
- **Persistent Chat History**: Your conversation context is preserved when you switch models.
- **Streaming Responses**: Responses from the assistant, including tool calls, are streamed live for a responsive feel.
- **Interactive Tool Use**: The assistant can request to execute `bash` and `python` scripts, which you can approve or deny before they run.
- **Configuration-Driven**:
    - `models.json`: Define the models you want to use with a unique display name for each model-provider pair.
    - `providers.json`: Configure the API endpoints and environment variables for each provider's API key.
- **Rich Terminal UI**:
    - Built with `rich` and `prompt_toolkit`.
    - Autocompletion for commands and file paths.
    - Optional UI borders for a cleaner look.
- **Shortcuts for Efficiency**:
    - `Tab`: Autocomplete commands and paths.
    - `Right Arrow`: Accept autocompletion suggestions.
    - `Ctrl+D`: Submit your message.
    - `Ctrl+C`: Exit the application or interrupt the AI's response.
    - `Ctrl+B`: Toggle UI borders on/off.
    - `Ctrl+E`: Clear the current input line.
- **Chat Management**:
    - Save and load chat sessions with the `o <filename>` command
    - Context management with `/pushContext` and `/popContext` commands
    - Persistent chat history across model switches
- **Memory System**: Persistent storage for context and useful information across sessions.
- **Command System**: Extensible command system with local and global commands.

## File Structure

```
chat.sh/
├── chat.py              # Main application entry point
├── chat_client.py       # Core chat client logic
├── completer.py         # Autocompletion system
├── executors.py         # Tool execution handlers
├── input_handler.py     # Input processing utilities
├── models.json          # Model definitions
├── providers.json       # API provider configuration
├── systemPrompt         # Base system prompt for AI
├── requirements.txt     # Python dependencies
├── chat.sh            # Launcher script
├── AI.md               # Project-specific directives for Egg
├── commands/           # Local command definitions
├── global_commands/    # Global command definitions
├── localChats/        # Saved chat sessions
└── memory/            # Persistent storage directory
```

## Setup

1.  **Dependencies**: Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Providers Configuration (`providers.json`)**:
    This file maps a provider name to its API endpoint and the environment variable that holds its API key.

    *Example `providers.json`*:
    ```json
    {
        "openai": {
            "api_base": "https://api.openai.com/v1/chat/completions",
            "api_key_env": "OPENAI_API_KEY"
        },
        "local": {
            "api_base": "http://localhost:11434/v1/chat/completions",
            "api_key_env": "LOCAL_API_KEY"
        }
    }
    ```

3.  **Models Configuration (`models.json`)**:
    This file defines the models you want to use. The key is a unique, human-friendly display name.

    *Example `models.json`*:
    ```json
    {
        "OpenAI GPT-4o": {
            "provider": "openai",
            "model_name": "gpt-4o",
            "max_tokens": 128000
        },
        "Local Llama3": {
            "provider": "local",
            "model_name": "llama3",
            "max_tokens": 8000
        }
    }
    ```

4.  **API Keys**: Set the environment variables defined in your `providers.json` with your API keys.
    ```bash
    export OPENAI_API_KEY="sk-..."
    # No key needed for default local provider
    ```

## Usage

Run the application with the `chat.sh` script:
```bash
./chat.sh
```

### Commands

- **/model `<display_name>`**: Switch the active AI model.

- **Context Commands**:
  - `/pushContext <description>` - Save current chat and start new context
  - `/popContext <return_value>` - Return to previous context and restore chat
  - Example: `/model Local Llama3`
  - Typing `/model ` and hitting `Tab` will show available models.

- **o `<chat_file_name>`**: Open (load) a previously saved chat session from the `localChats/` directory.
  - Supports autocompletion - type `o ` and hit `Tab` to see available chats.

- **b `<command>`**: Execute a local bash command and inject the output into the conversation history as context.
  - Example: `b ls -l`

- **/global/<command>**: Execute global commands from the global_commands directory.
  - Supports autocompletion for available commands.

- **/toggleYesToolFlag**: Toggle whether tool calls (bash/python execution) are automatically approved without user confirmation.

### Advanced Features

- **Memory System**: The assistant can store and retrieve information across sessions using the memory system.
- **Command Extensions**: Add custom functionality by creating new command files in the commands/ or global_commands/ directories.
- **Context Preservation**: Chat history and context persist across model switches and sessions.
- **Tool Integration**: The assistant can use bash and python tools to help with tasks, with user confirmation for security.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Tab | Autocomplete commands and file paths |
| Right Arrow | Accept autocompletion suggestion |
| Ctrl+D | Submit message |
| Ctrl+C | Exit application or interrupt response |
| Ctrl+B | Toggle UI borders on/off |
| Ctrl+E | Clear current input line |

## Configuration Files

- **AI.md**: Contains project-specific directives and instructions for Egg (this AI assistant)
- **systemPrompt**: The base system prompt sent to all AI models
- **models.json**: Model definitions and configurations
- **providers.json**: API endpoint configurations

## Development

The application is built with Python 3.7+ and uses:
- `prompt_toolkit` for terminal UI and autocompletion
- `rich` for rich text formatting
- `requests` for HTTP API calls
- JSON for configuration files

## Troubleshooting

- Ensure all required environment variables are set
- Check that models.json and providers.json are valid JSON
- Verify API endpoints are accessible
- Use `b ls localChats/` to see available saved chats
