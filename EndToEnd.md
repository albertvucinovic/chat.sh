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

*   Steps:
    1.  Run `./chat.sh` in the terminal.
    2.  Observe the initial welcome panel with instructions.
    3.  Type a simple message like "Hello" and press `Ctrl+D` to send.
    4.  Wait for the AI's response.
    5.  Press `Ctrl+C` to exit the application.
*   Expected Outcome:
    *   The welcome panel is displayed correctly.
    *   The AI responds to the message.
    *   A "Saving chat and exiting..." message appears.
    *   A message "Chat saved to: <path>" is displayed.
    *   A new JSON file corresponding to the chat is created in the `.egg/localChats/` directory.

### Test 1.2: Start with Missing API Key

*   Steps:
    1.  Unset the required API key environment variable (e.g., `unset OPENAI_API_KEY`).
    2.  Run `./chat.sh`.
    3.  Type a message like "Hello" and press `Ctrl+D` to send.
*   Expected Outcome:
    *   The application starts successfully and displays the welcome panel.
    *   An error message is printed in the terminal indicating that the environment variable for the provider is not set.
    *   The subsequent API call fails, resulting in an error message from the request (e.g., related to a 401 Unauthorized status).

---

## 2. User Interface and Input

### Test 2.1: Border Toggling

*   Steps:
    1.  Start the application.
    2.  Press `Ctrl+B`.
    3.  Send a message.
    4.  Press `Ctrl+B` again.
    5.  Send another message.
*   Expected Outcome:
    *   A message "Borders are now OFF" appears above the prompt.
    *   The subsequent message panel for "You" and "Assistant" should have no visible borders. The prompt should change from `[You]:` to `You:`.
    *   A message "Borders are now ON" appears.
    *   The next message panels have rounded borders again.

### Test 2.2: Multi-line Input and Clearing

*   Steps:
    1.  Start the application.
    2.  Type "This is the first line" and press `Enter`.
    3.  Type "This is the second line."
    4.  Press `Ctrl+E`.
    5.  Type a new single-line message and press `Ctrl+D`.
*   Expected Outcome:
    *   After pressing `Enter`, the cursor moves to a new line prefixed with `... ` or `[...]`.
    *   After pressing `Ctrl+E`, the entire multi-line input is cleared from the screen.
    *   The final message is sent correctly to the AI.

### Test 2.3: Autocompletion and Suggestion

*   Steps:
    1.  Start the application.
    2.  Type `/o ` (with a space) and press `Tab` to cycle tree ids or `list`.
    3.  Type a message, e.g., "The quick brown fox". Type another message, "The quick brown bear".
    4.  On a new line, type `The qui` and observe the greyed-out auto-suggestion.
    5.  Press the `Right Arrow` key.
*   Expected Outcome:
    *   Pressing `Tab` after `/o ` suggests available agent trees or `list`.
    *   The auto-suggestion should show `ck brown fox` or `ck brown bear`.
    *   Pressing `Right Arrow` accepts the suggestion, completing the text in the input buffer.

---

## 3. Command Handling

### Test 3.1: Local Bash Command

*   Steps:
    1.  Start the application.
    2.  Execute `b ls -l`.
    3.  After the output is shown, ask the AI "What do you see in the output above?".
*   Expected Outcome:
    *   A "Local Command Output" panel appears, showing the results of `ls -l`.
    *   The AI should be able to answer the question, demonstrating that the command output was sent as context.

### Test 3.2: Model Switching

*   Steps:
    1.  Start the application.
    2.  Execute `/model` to see a list of available models.
    3.  Execute `/model <some_other_valid_model_key>`.
*   Expected Outcome:
    *   A list of models from `models.json` is printed.
    *   A confirmation message "Switched to model: '<model_key>'" is printed.

---

## 4. Chat and Context Management

### Test 4.1: Load Chat History (Not exposed as a command)

*   Steps:
    1.  Ensure you have a saved chat file in `.egg/localChats/`.
    2.  Note: Loading past chats is not exposed as a slash command. You can view JSON files directly or add a future `/chats` command.
*   Expected Outcome:
    *   N/A (feature not exposed via slash command in current build).

## 5. AI Tool Calls

### Test 5.1: Tool Call with User Confirmation

*   Steps:
    1.  Start a session.
    2.  Ask the AI: "Use python to calculate 10 factorial and print the result."
    3.  When the confirmation prompt `Execute the python tool call? [y/n/a]` appears, type `y` and press `Enter`.
    4.  Ask the same question again.
    5.  When the confirmation prompt appears, type `n` and press `Enter`.
*   Expected Outcome:
    *   An assistant message with a "Tool Call: python" panel appears, showing the Python script.
    *   After confirming `y`, an "Execution Output" panel appears with the correct result (3628800). The AI then responds based on this output.
    *   After confirming `n`, a "Skipped by user" message appears, and the AI receives "--- SKIPPED BY USER ---" as the tool output.

### Test 5.2: Automatic Tool Call (`/toggleYesToolFlag`)

*   Steps:
    1.  Start a session.
    2.  Execute `/toggleYesToolFlag`.
    3.  Ask the AI: "Use the bash tool to echo the word 'hello'."
*   Expected Outcome:
    *   A message "TOOL CALLS WILL AUTOMATICALLY GO THROUGH" is printed.
    *   The AI's tool call is displayed and then executed immediately without a confirmation prompt.
    *   The "Execution Output" panel shows the result.

---

## 6. Streaming and Live Display

### Test 6.1: Live Content Streaming

*   Steps:
    1.  Start a session.
    2.  Ask a question that requires a long, multi-paragraph answer.
*   Expected Outcome:
    *   An initial panel appears ("Assistant is thinking...").
    *   The text from the assistant should appear token-by-token, streaming into the assistant panel in real-time.

### Test 6.2: Thinking/Reasoning Display

*   Steps:
    1.  Start a session.
    2.  Execute `/toggleThinkingDisplay`.
    3.  Ask the AI a question that might involve reasoning or a tool call.
    4.  Execute `/toggleThinkingDisplay` again.
    5.  Ask another complex question.
*   Expected Outcome:
    *   After the first toggle, a message "Thinking display is now OFF" is printed. During generation, only the "Assistant is thinking..." panel is visible until the final response is rendered.

---

## 7. Agent Trees, Subagents, and /wait

### Test 7.1: Fresh session creates a new tree and isolates /wait

- Steps:
  1. Run `./chat.sh` in a new terminal.
  2. Observe the "Started new agent tree: <TREE_ID>" panel.
  3. Run `/tree list`.
  4. Run `/wait any`.
- Expected Outcome:
  - The tree panel shows a fresh `<TREE_ID>`.
  - `/tree list` marks this `<TREE_ID>` with an asterisk.
  - `/wait any` returns immediately with `completed=[]` and `pending=[]` if there are no children.

### Test 7.2: Spawn subagent, finish with `/popContext`, parent `/wait` gets results

- Steps:
  1. Prepare a file `global_commands/sample.md` with any content.
  2. In parent: `/spawn global/sample.md Do the task`
  3. In child pane: verify Subagent Context, Initial Context, and How to Finish panels.
  4. In child: `/popContext ./output.md`
  5. In parent: `/wait any`
- Expected Outcome:
  - Child process exits after `popContext`.
  - Parent displays a "Wait Agents" panel showing JSON with the child in `completed` and `results[child_id].return_value == ./output.md`. A "Wait Results" panel summarizes the result.
  - The child pane is closed automatically when `/wait` completes for that child.

### Test 7.3: Per-run isolation with new tree

- Steps:
  1. Open a new terminal and run `./chat.sh` again.
  2. Observe a new "Started new agent tree: <NEW_TREE_ID>".
  3. `/tree list`
  4. `/wait any`
- Expected Outcome:
  - A brand-new tree id is created and marked as current.
  - `/wait any` returns empty results (does not consider previous session’s children).

### Test 7.4: Continue an existing tree via `/tree use`

- Steps:
  1. In the new session: `/tree list` and identify an older `<TREE_ID>`.
  2. `/tree use <TREE_ID>`
  3. `/wait all`
- Expected Outcome:
  - Panel confirms "Switched to tree: <TREE_ID>".
  - `/wait all` behaves according to children in that tree; completed results appear if any are done.

### Test 7.5: Wait for explicit child ids

- Steps:
  1. `/spawn global/sample.md quick-task`
  2. Note the child_id in the "Spawned Agent" panel (e.g., `sample-001`).
  3. `/wait sample-001`
- Expected Outcome:
  - `/wait` waits for exactly that child and returns its result when finished.

### Test 7.6: `/wait all` with no children returns immediately

- Steps:
  1. In a fresh session: `/wait all`
- Expected Outcome:
  - Immediate return with `completed=[]` and `pending=[]`; no hang.

## 8. tmux Attach

### Test 8.1: Attach to a tree’s tmux session

- Steps:
  1. `/tree list`, note current `<TREE_ID>`.
  2. `/attach <TREE_ID>`
- Expected Outcome:
  - tmux attaches; panes are organized per layer: left = parent’s pane, right column = stacked children for that parent.

Notes:
- Each fresh run of `./chat.sh` now creates a new tree unless `EG_TREE_ID` is pre-set or you switch via `/tree use`.
- `/wait` executes locally and shows both raw JSON and a summarized "Wait Results" panel.
- Subagents exit on `/popContext` and write `result.json` to their agent dir under the active tree.
- Child panes are closed by the parent upon `/wait` completion.
- Trees live under `.egg/agents/<tree_id>/...`, with the current tree id stored in `.egg/agents/.current_tree` and exported as `EG_TREE_ID`.
