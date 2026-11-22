#!/bin/zsh
# Motor kıyasının WebKit tarafını derler → build/engine-bench/WebKitBench.app (yalnız bu makinenin mimarisi, ad-hoc).
set -euo pipefail
here=${0:A:h}
root=${here:h:h}
out="$root/build/engine-bench/WebKitBench.app"
rm -rf "$out"
mkdir -p "$out/Contents/MacOS"
cat > "$out/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>WebKitBench</string>
  <key>CFBundleIdentifier</key><string>dev.canard.webkit-bench</string>
  <key>CFBundleName</key><string>WebKitBench</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Motor kıyası: içerik mikrofon isterse.</string>
  <key>NSCameraUsageDescription</key><string>Motor kıyası: içerik kamera isterse.</string>
</dict></plist>
PLIST
xcrun swiftc -O -o "$out/Contents/MacOS/WebKitBench" "$here/WebKitBench.swift"
codesign --force --sign - "$out" 2>/dev/null
echo "derlendi: $out"
