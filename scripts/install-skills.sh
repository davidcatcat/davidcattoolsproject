#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SKILLS_DIR="$REPO/skills"
DESTS=("$HOME/.claude/skills" "$HOME/.agents/skills")

if [ ! -d "$SKILLS_DIR" ]; then
  echo "error: skills directory not found: $SKILLS_DIR" >&2
  exit 1
fi

for dest in "${DESTS[@]}"; do
  mkdir -p "$dest"

  for skill in "$SKILLS_DIR"/*; do
    [ -d "$skill" ] || continue
    [ -f "$skill/SKILL.md" ] || continue

    name="$(basename "$skill")"
    target="$dest/$name"

    if [ -e "$target" ] && [ ! -L "$target" ]; then
      echo "skip: $target already exists and is not a symlink" >&2
      continue
    fi

    ln -sfn "$skill" "$target"
    echo "linked: $target -> $skill"
  done
done

echo
echo "Skills installed for Claude Code (~/.claude/skills)"
echo "and Agent Skills-compatible tools (~/.agents/skills)."
