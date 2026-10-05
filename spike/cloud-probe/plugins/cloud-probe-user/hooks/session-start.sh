#!/bin/sh
# Throwaway cloud probe: records that a SessionStart hook ran, then prints a
# marker the session sees as context.
[ "$CLAUDE_CODE_REMOTE" = true ] || exit 0
name=$1
dir=/var/tmp/cloud-probe
{ mkdir -p "$dir" && [ -w "$dir" ]; } 2>/dev/null || { dir=/tmp/cloud-probe; mkdir -p "$dir"; }
{
  date -u +%Y-%m-%dT%H:%M:%SZ
  echo "user=$(id -un) home=$HOME pwd=$PWD project_dir=$CLAUDE_PROJECT_DIR plugin_root=$CLAUDE_PLUGIN_ROOT"
  cat
  echo
} >>"$dir/hook-$name.log"
echo "CLOUD-PROBE: $name SessionStart hook ran"
