# Agentic Chat Client for Local OpenAI-Compatible Endpoint

This project provides a CLI chat client that connects to a OpenAI-compatible endpoint, preferably local but can be remote.
The client supports features like tool calling, streaming responses, and local command execution, all in well bellow a 1000 lines of code, so it is hackable.

## Features

- **Tool Calling**: Native support for OpenAI's tool-calling protocol, streaming tool calls
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
   python3.11 -m venv venv
   ```

2. **Install required packages**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Setup the model**:
   - use one provided by some openai api provider and get api key, model, and api base variables that way
   - use llama.cpp's llama-server, this works for my dual 24G cards setup, after downloading .gguf file from huggingface, and compiling llama.cpp with `make LLAMA_CURL=1` in llama.cpp source dir got from github
      ```bash
      bin/llama-server \
        --model ../vllm/Models/mistralai/Devstral-Small-2507-Q8_0.gguf \
        --threads -1 \
        --ctx-size 40000 \
        --cache-type-k q8_0 \
        --n-gpu-layers 99 \
        --seed 3407 \
        --prio 2 \
        --temp 0.15 \
        --repeat-penalty 1.0 \
        --min-p 0.01 \
        --top-k 64 \
        --top-p 0.95 \
        --jinja \
        --port 10000
    ```
    - use vllm, sglang, ...

4. **Set up environment variables**:
   Create a `.env` file in the root directory of the project. This file will be sourced by `chat.sh`:

   - local api:
   ```bash
   export API_BASE=http://localhost:10000/v1/chat/completions
   export API_KEY=<your local api key>
   export API_MODEL='../vllm/Models/mistralai/Devstral-Small-2507-Q8_0.gguf'

   - TogetherAI:
   ```bash
   export API_BASE=https://api.together.xyz/v1/chat/completions
   export API_KEY=<your togetherai api key>
   export API_MODEL=moonshotai/Kimi-K2-Instruct
   ```

   - Gemini OpenAI compatible api:
   ```bash
   #Gemini
   export API_KEY=<your gemini api key from google ai studio>
   export API_MODEL=gemini-2.5-flash
   export API_BASE=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions

   ```

5. **Run the chat client**:
   ```bash
   ./chat.sh
   ```

## Tools Available

The client exposes two primary tools to the model:

- `bash(script: str)`: Execute shell scripts via `/bin/bash`
- `python(script: str)`: Execute Python snippets in-process

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

`o ` + tab starts cycling through previous chats for you to Open.
If you Ctrl+D, the previous chat will load.

## License

This project is open source and available under the [MIT License](https://opensource.org/licenses/MIT).

## Contributing

Feel free to submit issues or pull requests. For major changes, please open an issue first to discuss what you would like to change.

## Acknowledgments

- OpenAI for the tool-calling protocol inspiration
- All model developers whose work is referenced in the configuration

Happy chatting!
