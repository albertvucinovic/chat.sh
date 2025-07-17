# Local first, hackable, agentic chat for the command line

This project provides a CLI chat client that connects to any OpenAI-compatible endpoint. It's designed to be local-first, lightweight (well under 1000 lines of Python), and easily hackable.

The client supports true tool calling, streaming for both text and tool code, and a customizable, minimalist interface, making it a powerful and pleasant tool for developers.

![Demo](egg.gif)

## Features

-   **True Tool Calling**: Native support for OpenAI's tool-calling protocol. The assistant can request `bash` or `python` execution, which you can approve or deny.
-   **Live Streaming**: Responses, including text and tool code, are streamed in real-time as they are generated.
-   **Customizable UI**: Press **`Ctrl+B`** to instantly toggle all UI elements like borders, titles, and line numbers on or off for a clean, copy-paste-friendly view. The input prompt changes from `[You]:` to `You:` to reflect the current mode.
-   **Dual Execution Modes**:
    -   **Agentic**: Let the model decide when to run code using its tools.
    -   **Manual**: Instantly run any local shell command by prefixing it with `b `.
-   **Interactive & Efficient**:
    -   Tab completion for file paths, chat history, and commands.
    -   Persistent history and session saving.
    -   Support for multiline input.
-   **Chat Persistence**: Conversations are automatically saved to the `localChats/` directory, allowing you to resume them later.
-   **Context Management**: Load previous chats to continue a conversation using the `o ` command.

## Requirements

-   Python 3.11+
-   Virtual environment support
-   Internet access for installing dependencies

## Setup Instructions

1.  **Create and activate a virtual environment**:
    ```bash
    python3.11 -m venv venv
    source venv/bin/activate
    ```

2.  **Install required packages**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Setup the model**:
    -   use one provided by some openai api provider and get api key, model, and api base variables that way
    -   use llama.cpp's llama-server, this works for my dual 24G cards setup, after downloading .gguf file from huggingface, and compiling llama.cpp with `make LLAMA_CURL=1` in llama.cpp source dir got from github
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
    -   use vllm, sglang, ...

4.  **Set up environment variables**:
    Create a `.env` file in the root directory of the project. This file will be sourced by `chat.sh`:

    -   local api:
        ```bash
        export API_BASE=http://localhost:10000/v1/chat/completions
        export API_KEY=<your local api key>
        export API_MODEL='../vllm/Models/mistralai/Devstral-Small-2507-Q8_0.gguf'
        ```

    -   TogetherAI:
        ```bash
        export API_BASE=https://api.together.xyz/v1/chat/completions
        export API_KEY=<your togetherai api key>
        export API_MODEL=moonshotai/Kimi-K2-Instruct
        ```

    -   Gemini:
        ```bash
        #Gemini
        export API_KEY=<your gemini api key from google ai studio>
        export API_MODEL=gemini-2.5-flash
        export API_BASE=https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
        ```

    -   OpenAI:
        ```bash
        export API_BASE=https://api.openai.com/v1/chat/completions
        export API_KEY=<your openai api key>
        export API_MODEL=o3-mini
        ```

    -   Anthropic:
        ```bash
        #I guess
        export API_BASE=https://api.anthropic.com/v1/chat/completions
        export API_MODEL=claude-opus-4-20250514
        export API_KEY=<your anthropic key>
        ```

5.  **Run the chat client**:
    ```bash
    ./chat.sh
    ```

## Tools Available

The client exposes two primary tools to the model which allow execution of local commands:

-   `bash(script: str)`: Execute shell scripts via `/bin/bash`
-   `python(script: str)`: Execute Python snippets in-process

When the assistant generates a tool call, it will be displayed in a code block and you will be prompted for confirmation before execution. Type `y` to confirm execution, otherwise the tool call will be skipped.

## Usage

### Keyboard Shortcuts

-   **`Ctrl+D`**: Submit your multiline input to the assistant.
-   **`Ctrl+C`**: Interrupt a streaming response, or exit the application from the prompt (saves chat).
-   **`Ctrl+B`**: Toggle borders, panel titles, and line numbers on/off.
-   **`Tab`**: Autocomplete file paths, words from history, or special commands.

### Basic Chat

Type your message. Use Enter for new lines. Press `Ctrl+D` when you're done.

### Local Command Execution

To bypass the assistant and run a shell command directly, prefix it with `b `:

b ls -la


### Chat Management

To load a previous chat, type `o ` (o + space) and press `Tab` to cycle through saved chat files. Once you see the desired file, press `Ctrl+D` to load it.

## License

This project is open source and available under the [MIT License](https://opensource.org/licenses/MIT).

## Contributing

Feel free to submit issues or pull requests.
