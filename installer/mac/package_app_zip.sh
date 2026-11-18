#!/bin/bash
# Wraps build/PolicyLookup (assembled with `assemble.py --for-mac`) into
# "Policy Lookup.app" and zips it. Runs on Linux — used when no GitHub Mac
# machine is available. Users unzip and drag the app to Applications.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/build/PolicyLookup"
VERSION="${1:-1.0.0}"
OUTDIR="$ROOT/build/macapp"
APP="$OUTDIR/Policy Lookup.app"
rm -rf "$OUTDIR"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp -a "$SRC/python" "$SRC/ollama" "$SRC/app" "$APP/Contents/Resources/"
cp "$ROOT/build/icons/icon.icns" "$APP/Contents/Resources/AppIcon.icns"
cat > "$APP/Contents/MacOS/PolicyLookup" <<'SH'
#!/bin/bash
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$R/python/bin/python3.12" "$R/app/desktop_launcher.py"
SH
chmod +x "$APP/Contents/MacOS/PolicyLookup" "$APP/Contents/Resources/ollama/ollama"
sed "s/__VERSION__/$VERSION/g" "$ROOT/installer/mac/Info.plist" > "$APP/Contents/Info.plist"
mkdir -p "$ROOT/build/installers"
(cd "$OUTDIR" && zip -qry "$ROOT/build/installers/PolicyLookup-Mac.zip" "Policy Lookup.app")
echo "built $ROOT/build/installers/PolicyLookup-Mac.zip"
