#!/usr/bin/env bash
set -e
TREE_ID="${1:-default}"
ROOT=".egg/agents/$TREE_ID"
if [ ! -d "$ROOT" ]; then
  echo "No such tree: $TREE_ID"
  exit 1
fi
echo "Tree: $TREE_ID"
find "$ROOT" -maxdepth 3 -type d | sed "s|$ROOT||" | sed '1d' | awk -F/ '
BEGIN{indent="  "}
{
  if(NF==1 && $1!=""){print "- " $1}
  else if(NF==2 && $2!=""){print indent "- " $1 "/" $2}
  else if(NF==3 && $3!=""){print indent indent "- " $1 "/" $2 "/" $3}
}
'
