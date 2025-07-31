#!/usr/bin/env bash
set -e
TREE_ID="$1"
AGENT_ID="$2"
if [ -z "$TREE_ID" ]; then
  echo "Usage: attach_agent.sh <tree_id> [agent_id]"
  exit 1
fi
SESSION="egg-tree-$TREE_ID"
if [ -n "$AGENT_ID" ]; then
  tmux select-window -t "$SESSION:$AGENT_ID" 2>/dev/null || true
fi
TMUX=${TMUX:-}
if [ -n "$TMUX" ]; then
  tmux switch-client -t "$SESSION" || tmux attach -t "$SESSION"
else
  tmux attach -t "$SESSION"
fi
