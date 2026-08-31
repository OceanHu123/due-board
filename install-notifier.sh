#!/bin/zsh
# Build + install DueBoard.app so notification clicks open the due HTML page.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC_SWIFT="$ROOT/DueBoard.swift"
APP="$ROOT/DueBoard.app"
DEST="$HOME/Applications/DueBoard.app"
BIN="$APP/Contents/MacOS/DueBoard"

# Fall back to the old filename if it still exists (transition period).
if [[ ! -f "$SRC_SWIFT" ]]; then
  if [[ -f "$ROOT/UsydDueReminders.swift" ]]; then
    SRC_SWIFT="$ROOT/UsydDueReminders.swift"
  else
    echo "Missing DueBoard.swift (or UsydDueReminders.swift fallback)" >&2
    exit 1
  fi
fi

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>DueBoard</string>
	<key>CFBundleIdentifier</key>
	<string>com.oakley.due-board</string>
	<key>CFBundleName</key>
	<string>DueBoard</string>
	<key>CFBundleDisplayName</key>
	<string>DueBoard</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>2.0</string>
	<key>CFBundleVersion</key>
	<string>3</string>
	<key>LSMinimumSystemVersion</key>
	<string>13.0</string>
	<key>LSUIElement</key>
	<true/>
	<key>NSUserNotificationAlertStyle</key>
	<string>banner</string>
	<key>NSHighResolutionCapable</key>
	<true/>
</dict>
</plist>
PLIST

echo "Compiling notifier…"
xcrun swiftc -O \
  -framework AppKit \
  -framework UserNotifications \
  -o "$BIN" \
  "$SRC_SWIFT"

mkdir -p "$HOME/Applications"
rm -rf "$DEST"
cp -R "$APP" "$DEST"
codesign --force --deep --sign - "$DEST" >/dev/null
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$DEST" >/dev/null

echo "Installed $DEST"
echo "Try:  uv run due-board --open"
echo "Then: uv run due-board --test   # click the banner → due page"
echo "系统设置 → 通知 → DueBoard → 允许通知"
