#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents/com.oakley.due-board.plist"
mkdir -p "$HOME/.local/state/due-board"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$ROOT/com.oakley.due-board.plist" "$DEST"
# Unload previous label first (old & new names, safe even if they don't exist)
launchctl bootout "gui/$(id -u)/com.oakley.due-board" 2>/dev/null || true
launchctl bootout "gui/$(id -u)/com.oakley.usyd-due-reminders" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Loaded $DEST"
echo "Unload: launchctl bootout gui/$(id -u)/com.oakley.due-board"
