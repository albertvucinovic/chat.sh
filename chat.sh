#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse optional CLI flags: --tree <id>, --inline
TREE_CLI=""
RUN_INLINE=0
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
  arg=${args[$i]}
  if [[ "$arg" == "--tree" ]]; then
    j=$((i+1))
    if [[ $j -lt ${#args[@]} ]]; then
      TREE_CLI=${args[$j]}
      i=$((i+1))
    fi
  elif [[ "$arg" == "--inline" ]]; then
    RUN_INLINE=1
  fi
  i=$((i+1))
done

# Decide TREE_ID policy: always new unless provided via --tree
if [[ -n "$TREE_CLI" ]]; then
  export EG_TREE_ID="$TREE_CLI"
else
  export EG_TREE_ID="$(date +%s)"
fi
TREE_ID="$EG_TREE_ID"

# Activate the virtual environment
source "$SCRIPT_DIR/venv/bin/activate"

# Load environment variables (API keys, DEFAULT_MODEL, etc.)
# Make these available to any spawned tmux panes/windows as well
set -a
source "$SCRIPT_DIR/.env"
set +a

# Write current tree marker and ensure dirs
mkdir -p "$SCRIPT_DIR/.egg/agents"
echo "$TREE_ID" > "$SCRIPT_DIR/.egg/agents/.current_tree"
mkdir -p "$SCRIPT_DIR/.egg/agents/$TREE_ID/root"

SESSION="egg-tree-$TREE_ID"

if [[ $RUN_INLINE -eq 1 ]]; then
  # Explicit inline run (used for spawned children)
  python "$SCRIPT_DIR/chat.py" "$@"
  deactivate
  exit 0
fi

# Always bootstrap per-tree tmux session
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" 'bash'
  tmux rename-window -t "$SESSION":0 root
  tmux send-keys -t "$SESSION":root \
    "cd '$SCRIPT_DIR' && source venv/bin/activate && set -a && source .env && set +a && EG_TREE_ID='$TREE_ID' bash -lc 'python chat.py'" C-m
fi

# Announce target session
echo "[egg] TREE_ID=$TREE_ID SESSION=$SESSION"

# If inside tmux, switch this client to the target session; otherwise attach
if [[ -n "${TMUX:-}" ]]; then
  if tmux switch-client -t "$SESSION" 2>/dev/null; then
    deactivate
    exit 0
  else
    # As a fallback, attempt to run-shell a switch for this client
    tmux run-shell -b "tmux switch-client -t '$SESSION'" || true
    echo "[egg] Could not switch client automatically. Run: tmux switch-client -t $SESSION"
    deactivate
    exit 0
  fi
else
  tmux attach -t "$SESSION"
  deactivate
  exit 0
fi
