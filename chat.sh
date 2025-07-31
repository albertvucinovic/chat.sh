#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate the virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Load environment variables (API keys, DEFAULT_MODEL, etc.)
# Make these available to any spawned tmux panes/windows as well
set -a
source "$SCRIPT_DIR/.env"
set +a

# Step 2: tmux auto-bootstrap for per-run tree sessions (opt-out with EG_TMUX_AUTO=0)
if [[ -z "${TMUX:-}" && "${EG_TMUX_AUTO:-1}" != "0" ]]; then
  # Ensure tree id (reuse if provided via env)
  if [[ -z "${EG_TREE_ID:-}" ]]; then
    TREE_ID="$(date +%s)"
    mkdir -p "$SCRIPT_DIR/.egg/agents"
    echo "$TREE_ID" > "$SCRIPT_DIR/.egg/agents/.current_tree"
    export EG_TREE_ID="$TREE_ID"
  else
    TREE_ID="$EG_TREE_ID"
    mkdir -p "$SCRIPT_DIR/.egg/agents"
    echo "$TREE_ID" > "$SCRIPT_DIR/.egg/agents/.current_tree"
  fi

  SESSION="egg-tree-$TREE_ID"

  # Create session if missing and start root window running chat.py under venv and .env
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" 'bash'
    tmux rename-window -t "$SESSION":0 root
    # Start the root chat in the root window with the correct env and venv
    tmux send-keys -t "$SESSION":root \
      "cd '$SCRIPT_DIR' && source venv/bin/activate && set -a && source .env && set +a && EG_TREE_ID='$TREE_ID' bash -lc 'python chat.py $*'" C-m
  fi

  # Attach or switch to the session
  if tmux switch-client -t "$SESSION" 2>/dev/null; then
    deactivate
    exit 0
  else
    tmux attach -t "$SESSION"
    deactivate
    exit 0
  fi
fi

# If we’re here, run inline (no tmux auto-attach or already in tmux)
python "$SCRIPT_DIR/chat.py" "$@"

# Deactivate the virtual environment
deactivate
