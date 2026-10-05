#!/bin/bash
# Throwaway cloud probe: prints what a cloud session can see. Run it inside the
# session started on the cloud-probe branch.
for dir in /var/tmp/cloud-probe /tmp/cloud-probe; do
  [ -d "$dir" ] || continue
  for f in "$dir"/*.log; do
    echo "===== $f"
    cat "$f"
  done
done
echo "===== session"
set -x
id
echo "HOME=$HOME PWD=$PWD CLAUDE_CODE_REMOTE=$CLAUDE_CODE_REMOTE"
echo "PATH=$PATH"
command -v claude cloud-probe uv python3
cloud-probe
claude plugin list
claude plugin marketplace list
ls -la "$HOME/.claude" "$HOME/.claude/plugins"
cat "$HOME/.claude/settings.json"
