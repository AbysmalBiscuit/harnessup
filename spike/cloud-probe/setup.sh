#!/bin/bash
# Throwaway cloud probe for the setup phase. Paste this file into a test cloud
# environment's setup script. Every step logs to /var/tmp/cloud-probe/setup.log
# and the script always exits 0, so a failed step never blocks the session.
REF=cloud-probe
LOG=/var/tmp/cloud-probe
mkdir -p "$LOG" && chmod 1777 "$LOG"
exec >>"$LOG/setup.log" 2>&1
set -x
date -u
id
echo "HOME=$HOME PWD=$PWD SHELL=$SHELL CLAUDE_CODE_REMOTE=$CLAUDE_CODE_REMOTE CLAUDE_PROJECT_DIR=$CLAUDE_PROJECT_DIR"
echo "PATH=$PATH"
env | cut -d= -f1 | sort | tr '\n' ' '
echo

# Is the session's repository present yet, and where?
ls -la
git rev-parse --show-toplevel
git remote -v
find / -maxdepth 4 -name .git -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null | head -20

command -v claude uv python3 gh git
claude --version
uv --version
python3 --version

# Plugin installed by the setup script at user scope.
git clone --depth 1 --branch "$REF" https://github.com/AbysmalBiscuit/cloud-agent /opt/cloud-probe
chmod -R a+rX /opt/cloud-probe
claude plugin marketplace add /opt/cloud-probe
claude plugin install cloud-probe-user@cloud-probe --scope user
claude plugin list
cat "$HOME/.claude/settings.json"

# A CLI installed with uv from a git URL.
uv tool install "git+https://github.com/AbysmalBiscuit/cloud-agent@$REF#subdirectory=spike/cloud-probe/cli"
uv tool dir --bin
command -v cloud-probe && cloud-probe

date -u
exit 0
