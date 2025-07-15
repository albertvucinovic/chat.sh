# Chat Client for Local OpenAI-Compatible Endpoint

This project provides a CLI chat client that connects to a locally-hosted OpenAI-compatible endpoint. The client supports advanced features like tool calling, streaming responses, and local command execution.

## Features

- **Tool Calling**: Native support for OpenAI's tool-calling protocol
- **Streaming Responses**: Real-time output of generated code
- **Local Execution**: Built-in tools for running bash scripts and Python code
- **Environment Management**: Easy setup with virtual environments and dependencies
- **Interactive Interface**: Tab completion, history-based suggestions, and keyboard shortcuts
- **Chat Persistence**: Automatic saving of conversation history
- **Context Management**: Ability to load previous chats and maintain context

## Requirements

- Python 3.11+
- Virtual environment support
- Internet access for installing dependencies

## Setup Instructions

1. **Create and activate a virtual environment**:
   ```bash
   python3.11 -m venv python3.11-venv
   source python3.11-venv/bin/activate
   ```

2. **Install required packages**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up environment variables**:
   Create a `.env` file with your OpenAI API key and model configuration:
   ```bash
   export API_KEY='your-api-key'
   export API_MODEL='path/to/your/model'
   export API_BASE=http://localhost:10000
   ```

4. **Run the chat client**:
   ```bash
   ./chat.sh
   ```

## Tools Available

The client exposes two primary tools to the model:

- `bash(script: str)`: Execute shell scripts via `/bin/bash`
- `python(script: str)`: Execute Python snippets in-process

## Configuration

The `.env` file contains various model configuration options. Uncomment and modify the appropriate lines to select your desired model:

```bash
# export API_MODEL='Models/Qwen/Qwen3-30B-A3B-Q8_0.gguf'
# export API_MODEL='../vllm/Models/Qwen/Qwen3-30B-A3B-Q8_0.gguf'
# export API_MODEL='Qwen/Qwen3-32B-AWQ'
# export API_MODEL='Models/mistralai/Devstral-Small-2507-Q8_0.gguf'
# export API_MODEL='Valdemardi/DeepSeek-R1-Distill-Qwen-32B-AWQ'
# export API_MODEL='kosbu/Llama-3.3-70B-Instruct-AWQ'
# export API_MODEL='Qwen/Qwen2.5-Coder-32B-Instruct-AWQ'
# export API_MODEL='KirillR/QwQ-32B-Preview-AWQ'
# export API_MODEL='Qwen/Qwen2.5-72B-Instruct-AWQ'
```

## Usage

### Basic Chat

Simply type your messages and press Ctrl+D to submit. The assistant will respond with streaming output.

### Local Command Execution

Prefix your command with `b ` to execute it locally:
```
b ls -la
```

### Keyboard Shortcuts

- **Ctrl+D**: Submit input
- **Ctrl+C**: Save chat and exit
- **Ctrl+E**: Clear input
- **Ctrl+I**: Interrupt the generation
- **Tab**: Autocomplete words from history or filesystem
- **Shift+Tab**: Cycle through previous suggestions

### Chat Management

- **List saved chats**:
  ```bash
  ./chat.sh --list
  ```

- **Load a chat**:
  ```bash
  ./chat.sh --load chat_file.json
  ```

## License

This project is open source and available under the [MIT License](https://opensource.org/licenses/MIT).

## Contributing

Feel free to submit issues or pull requests. For major changes, please open an issue first to discuss what you would like to change.

## Acknowledgments

- OpenAI for the tool-calling protocol inspiration
- All model developers whose work is referenced in the configuration

Happy chatting! ð
