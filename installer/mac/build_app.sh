#!/bin/bash
# Wraps build/PolicyLookup into "Policy Lookup.app" and a drag-to-install .dmg.
# Run on an Apple Silicon Mac (GitHub Actions) after installer/assemble.py.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/build/PolicyLookup"
APP="$ROOT/build/dmg/Policy Lookup.app"
VERSION="${1:-1.0.0}"
rm -rf "$ROOT/build/dmg"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -R "$SRC/python" "$SRC/ollama" "$SRC/app" "$APP/Contents/Resources/"
cp "$ROOT/build/icons/icon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/MacOS/PolicyLookup" <<'SH'
#!/bin/bash
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$R/python/bin/python3.12" "$R/app/desktop_launcher.py"
SH
chmod +x "$APP/Contents/MacOS/PolicyLookup"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Policy Lookup</string>
  <key>CFBundleDisplayName</key><string>Policy Lookup</string>
  <key>CFBundleIdentifier</key><string>local.policylookup.app</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleExecutable</key><string>PolicyLookup</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
</dict></plist>
PLIST
# Ad-hoc signature so macOS accepts the bundled programs (not a notarised Developer ID).
codesign --force --deep --sign - "$APP" || true
ln -s /Applications "$ROOT/build/dmg/Applications"
mkdir -p "$ROOT/build/installers"
hdiutil create -volname "Policy Lookup" -srcfolder "$ROOT/build/dmg" -ov -format UDZO \
  "$ROOT/build/installers/PolicyLookup-Mac.dmg"
echo "built $ROOT/build/installers/PolicyLookup-Mac.dmg"
