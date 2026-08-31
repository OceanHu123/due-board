#!/bin/zsh
# Build + install UsydDueReminders.app so notification clicks open the due HTML page.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC_SWIFT="$ROOT/UsydDueReminders.swift"
APP="$ROOT/UsydDueReminders.app"
DEST="$HOME/Applications/UsydDueReminders.app"
BIN="$APP/Contents/MacOS/UsydDueReminders"

if [[ ! -f "$SRC_SWIFT" ]]; then
  echo "Missing $SRC_SWIFT" >&2
  exit 1
fi

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CFBundleExecutable</key>
	<string>UsydDueReminders</string>
	<key>CFBundleIdentifier</key>
	<string>com.oakley.usyd-due-reminders</string>
	<key>CFBundleName</key>
	<string>UsydDueReminders</string>
	<key>CFBundleDisplayName</key>
	<string>Usyd Due Reminders</string>
	<key>CFBundlePackageType</key>
	<string>APPL</string>
	<key>CFBundleShortVersionString</key>
	<string>1.1</string>
	<key>CFBundleVersion</key>
	<string>2</string>
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
echo "Try:  uv run python remind.py --open"
echo "Then: uv run python remind.py --test   # click the banner → due page"
echo "系统设置 → 通知 → Usyd Due Reminders → 允许通知"
