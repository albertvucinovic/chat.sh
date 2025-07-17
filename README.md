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
-   An OpenAI-compatible API endpoint (local or remote)

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

3.  **Setup a model endpoint**:
    This client can connect to any OpenAI-compatible API.
    -   **Local (Recommended)**: Use tools like `llama-cpp-python`'s server, vLLM, or SGLang. For `llama.cpp`, compile it with `make LLAMA_CURL=1` and run the server:
        ```bash
        ./server -m "path/to/your/model.gguf" --n-gpu-layers 99 --port 10000
        ```
    -   **Remote**: Use a commercial API provider.

4.  **Set up environment variables**:
    Create a `.env` file in the project root. The `chat.sh` script will automatically source it.

    **Local Example:**
    ```bash
    export API_BASE="http://localhost:10000/v1"
    export API_KEY="sk-local" # Can be anything for most local servers
    export API_MODEL="local-model" # Name used by the local server
    ```

    **TogetherAI Example:**
    ```bash
    export API_BASE="https://api.together.xyz/v1"
    export API_KEY="<your-togetherai-api-key>"
    export API_MODEL="mistralai/Mixtral-8x7B-Instruct-v0.1"
    ```

5.  **Run the chat client**:
    ```bash
    ./chat.sh
    ```

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


### Model Tool Execution

The assistant can generate `bash` or `python` code blocks. You will be prompted to confirm execution with `y` or `n` before they run.

### Chat Management

To load a previous chat, type `o ` (o + space) and press `Tab` to cycle through saved chat files. Once you see the desired file, press `Ctrl+D` to load it.

## License

This project is open source and available under the [MIT License](https://opensource.org/licenses/MIT).

## Contributing

Feel free to submit issues or pull requests.
