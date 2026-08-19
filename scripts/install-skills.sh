#!/usr/bin/env bash
# Install the memory-* agentic-memory skills into an agent skills directory.
#
#   ./install.sh                       # → ~/.claude/skills
#   ./install.sh --target ~/.agent/skills
#   ./install.sh --target ./.claude/skills   # project-local
#
# Copies each memory-*/ skill folder into the target dir (overwriting same-named
# skills). Depends only on a POSIX shell + coreutils.
#
# These skills are the primitive bindings for the agentic-memory MCP surface;
# install the package too (`uv tool install agentic-memory-system`, or from the
# release wheel) so `agentic-memory-mcp` and `agentic-memory-gui` are on PATH.
set -euo pipefail

TARGET="${HOME}/.claude/skills"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${HERE}/skills"

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [ ! -d "$SRC" ]; then
  echo "error: no skills/ dir next to this installer ($SRC)" >&2
  exit 1
fi

mkdir -p "$TARGET"
count=0
for skill in "$SRC"/memory-*/; do
  [ -d "$skill" ] || continue
  name="$(basename "$skill")"
  rm -rf "${TARGET:?}/${name}"
  cp -R "$skill" "$TARGET/$name"
  count=$((count + 1))
done
echo "installed $count memory-* skills → $TARGET"
