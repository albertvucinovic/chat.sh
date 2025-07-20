# End-to-End Tests for chat.sh

This document outlines the manual end-to-end tests required to verify the complete functionality of the `chat.sh` command-line interface application.

## Prerequisites

1.  The application is installed and the `chat.sh` script is executable.
2.  A valid `models.json` and `providers.json` are present.
3.  Required environment variables (e.g., `OPENAI_API_KEY`) are set for the default model.
4.  The `global_commands` directory exists and contains at least one sample command file (e.g., `pirate_task.md`).

---

## 1. Core Application Lifecycle

### Test 1.1: Clean Start and Graceful Exit

*   **Steps:**
    1.  Run `./chat.sh` in the terminal.
    2.  Observe the initial welcome panel with instructions.
    3.  Type a simple message like "Hello" and press `Ctrl+D` to send.
    4.  Wait for the AI's response.
    5.  Press `Ctrl+C` to exit the application.
*   **Expected Outcome:**
    *   The welcome panel is displayed correctly.
    *   The AI responds to the message.
    *   A "Saving chat and exiting..." message appears.
    *   A message "Chat saved to: <path>" is displayed.
    *   A new JSON file corresponding to the chat is created in the `localChats/` directory.

### Test 1.2: Start with Missing API Key

*   **Steps:**
    1.  Unset the required API key environment variable (e.g., `unset OPENAI_API_KEY`).
    2.  Run `./chat.sh`.
    3.  Type a message like "Hello" and press `Ctrl+D` to send.
*   **Expected Outcome:**
    *   The application starts successfully and displays the welcome panel.
    *   An error message is printed in the terminal indicating that the environment variable for the provider is not set.
    *   The subsequent API call fails, resulting in an error message from the request (e.g., related to a 401 Unauthorized status).

---

## 2. User Interface and Input

### Test 2.1: Border Toggling

*   **Steps:**
    1.  Start the application.
    2.  Press `Ctrl+B`.
    3.  Send a message.
    4.  Press `Ctrl+B` again.
    5.  Send another message.
*   **Expected Outcome:**
    *   A message "Borders are now OFF" appears above the prompt.
    *   The subsequent message panel for "You" and "Assistant" should have no visible borders. The prompt should change from `[You]:` to `You:`.
    *   A message "Borders are now ON" appears.
    *   The next message panels have rounded borders again.

### Test 2.2: Multi-line Input and Clearing

*   **Steps:**
    1.  Start the application.
    2.  Type "This is the first line" and press `Enter`.
    3.  Type "This is the second line."
    4.  Press `Ctrl+E`.
    5.  Type a new single-line message and press `Ctrl+D`.
*   **Expected Outcome:**
    *   After pressing `Enter`, the cursor moves to a new line prefixed with `... ` or `[...]`.
    *   After pressing `Ctrl+E`, the entire multi-line input is cleared from the screen.
    *   The final message is sent correctly to the AI.

### Test 2.3: Autocompletion and Suggestion

*   **Steps:**
    1.  Start the application.
    2.  Type `o ` (with a space) and press `Tab`.
    3.  Type `/pushContext global/` and press `Tab`.
    4.  Type a message, e.g., "The quick brown fox". Type another message, "The quick brown bear".
    5.  On a new line, type `The qui` and observe the greyed-out auto-suggestion.
    6.  Press the `Right Arrow` key.
*   **Expected Outcome:**
    *   Pressing `Tab` after `o ` should cycle through available chat files in `localChats/`.
    *   Pressing `Tab` after `/pushContext global/` should cycle through available files in `global_commands/`.
    *   The auto-suggestion should show `ck brown fox` or `ck brown bear`.
    *   Pressing `Right Arrow` accepts the suggestion, completing the text in the input buffer.

---

## 3. Command Handling

### Test 3.1: Local Bash Command

*   **Steps:**
    1.  Start the application.
    2.  Execute `b ls -l`.
    3.  After the output is shown, ask the AI "What do you see in the output above?".
*   **Expected Outcome:**
    *   A "Local Command Output" panel appears, showing the results of `ls -l`.
    *   The AI should be able to answer the question, demonstrating that the command output was sent as context.

### Test 3.2: Model Switching

*   **Steps:**
    1.  Start the application.
    2.  Execute `/model` to see a list of available models.
    3.  Execute `/model <some_other_valid_model_key>`.
*   **Expected Outcome:**
    *   A list of models from `models.json` is printed.
    *   A confirmation message "Switched to model: '<model_key>'" is printed.

---

## 4. Chat and Context Management

### Test 4.1: Load Chat History

*   **Steps:**
    1.  Have a saved chat file in `localChats/` (e.g., `20250720_143000__chat_summary.json`).
    2.  Start a new session.
    3.  Execute `o 20250720_143000` (a unique part of the filename).
*   **Expected Outcome:**
    *   The screen clears.
    *   The entire conversation from the loaded file is rendered on screen.
    *   A "--- End of loaded conversation ---" message appears at the bottom.

### Test 4.2: Push and Pop Context (Text)

*   **Steps:**
    1.  Start a session and ask "What is the capital of France?". Wait for the answer "Paris".
    2.  Execute `/pushContext Let's talk about something else. What is the capital of Germany?`.
    3.  After the AI answers "Berlin", execute `/popContext The capital of Germany is Berlin.`.
    4.  Ask the AI "What was the answer to my very first question?".
*   **Expected Outcome:**
    *   After `/pushContext`, the screen clears and a new conversation begins about Germany.
    *   After `/popContext`, the screen clears, and the original conversation about France is restored.
    *   A new user message is appended: "Return value from push/pop context: The capital of Germany is Berlin."
    *   The AI should correctly answer "Paris", demonstrating it remembers the restored context.

### Test 4.3: Push and Pop Context (File-based Task)

*   **Steps:**
    1.  Create a file `task.md` with the content: `Please list the first three planets of the solar system, then say you are done.`
    2.  Create a file in `global_commands/` named `pirate_task.md` with the content: `Speak like a pirate and tell me the primary colors.`
    3.  Start a session.
    4.  Execute `/pushContext task.md`.
    5.  After the AI responds and calls `popContext`, start a new line.
    6.  Execute `/pushContext global/pirate_task.md`.
*   **Expected Outcome:**
    *   **For `task.md`:**
        *   The screen clears, and a new context is pushed containing the content of `task.md` prepended with a system note about calling `popContext`. The full content is visible.
        *   The AI lists Mercury, Venus, and Earth.
        *   The AI automatically calls the `popContext` tool with a summary (e.g., "Listed the first three planets.").
        *   The original (empty) context is restored.
    *   **For `global/pirate_task.md`:**
        *   The screen clears, and a new context is pushed from the global command file.
        *   The AI responds in a pirate voice, listing Red, Yellow, and Blue.
        *   The AI automatically calls `popContext`.
        *   The previous context is restored.

---

## 5. AI Tool Calls

### Test 5.1: Tool Call with User Confirmation

*   **Steps:**
    1.  Start a session.
    2.  Ask the AI: "Use python to calculate 10 factorial and print the result."
    3.  When the confirmation prompt `Execute the python tool call shown above? [y/N]:` appears, type `y` and press `Enter`.
    4.  Ask the same question again.
    5.  When the confirmation prompt appears, type `n` and press `Enter`.
*   **Expected Outcome:**
    *   An assistant message with a "Tool Call: python" panel appears, showing the Python script.
    *   After confirming `y`, an "Execution Output" panel appears with the correct result (3628800). The AI then responds based on this output.
    *   After confirming `n`, a "Skipped by user" message appears, and the AI receives "--- SKIPPED BY USER ---" as the tool output.

### Test 5.2: Automatic Tool Call (`/toggleYesToolFlag`)

*   **Steps:**
    1.  Start a session.
    2.  Execute `/toggleYesToolFlag`.
    3.  Ask the AI: "Use the bash tool to echo the word 'hello'."
*   **Expected Outcome:**
    *   A message "TOOL CALLS WILL AUTOMATICALLY GO THROUGH" is printed.
    *   The AI's tool call is displayed and then executed immediately without a confirmation prompt.
    *   The "Execution Output" panel shows the result.

---

## 6. Streaming and Live Display

### Test 6.1: Live Content Streaming

*   **Steps:**
    1.  Start a session.
    2.  Ask a question that requires a long, multi-paragraph answer.
*   **Expected Outcome:**
    *   An initial panel appears ("Assistant is thinking...").
    *   The text from the assistant should appear token-by-token, streaming into the assistant panel in real-time.

### Test 6.2: Thinking/Reasoning Display

*   **Steps:**
    1.  Start a session.
    2.  Execute `/toggleThinkingDisplay`.
    3.  Ask the AI a question that might involve reasoning or a tool call.
    4.  Execute `/toggleThinkingDisplay` again.
    5.  Ask another complex question.
*   **Expected Outcome:**
    *   After the first toggle, a message "Thinking display is now OFF" is printed. During generation, only the "Assistant is thinking..." panel is visible until the final response is rendered.
    *   After the second toggle, a message "Thinking display is now ON" is printed. During generation, a "Reasoning" panel should appear (if the model provides that data) and stream content live, alongside the main assistant response panel.
