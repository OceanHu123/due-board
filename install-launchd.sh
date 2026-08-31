#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/Library/LaunchAgents/com.oakley.usyd-due-reminders.plist"
mkdir -p "$HOME/.local/state/usyd-due-reminders"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$ROOT/com.oakley.usyd-due-reminders.plist" "$DEST"
launchctl bootout "gui/$(id -u)/com.oakley.usyd-due-reminders" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Loaded $DEST"
echo "Unload: launchctl bootout gui/$(id -u)/com.oakley.usyd-due-reminders"
