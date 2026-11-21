#!/bin/zsh
# Faz 3a spike B1/B2: CanardProbe.saver'ı Xcode olmadan (Command Line Tools) derler.
# Ana sınıf: PRINCIPAL=CanardProbeView (B1, WKWebView) | CanardFrameProbeView (B2-1, kare aktarımı; varsayılan)
#   tools/saver-probe/build.sh            → build/CanardProbe.saver
#   tools/saver-probe/build.sh --install  → ~/Library/Screen Savers/ altına kopyalar
# Kaldırmak: rm -rf ~/Library/Screen\ Savers/CanardProbe.saver
set -euo pipefail
here=${0:A:h}
out="$here/../../build/CanardProbe.saver"
principal="${PRINCIPAL:-CanardFrameProbeView}"
rm -rf "$out"
mkdir -p "$out/Contents/MacOS"
cat > "$out/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDevelopmentRegion</key><string>English</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleSupportedPlatforms</key><array><string>MacOSX</string></array>
  <key>CFBundleExecutable</key><string>CanardProbe</string>
  <key>CFBundleIdentifier</key><string>dev.canard.probe.saver</string>
  <key>CFBundleName</key><string>Canard Probe</string>
  <key>CFBundlePackageType</key><string>BNDL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>NSPrincipalClass</key><string>PRINCIPAL_CLASS</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
PLIST
sed -i '' "s/PRINCIPAL_CLASS/$principal/" "$out/Contents/Info.plist"
# Apple'ın kendi .saver'ları gibi evrensel ikili (arm64 + x86_64).
tmp=$(mktemp -d)
for arch in arm64 x86_64; do
  xcrun swiftc -O -target "$arch-apple-macos14.0" -module-name CanardProbe -emit-library \
    -o "$tmp/CanardProbe-$arch" -Xlinker -bundle -framework ScreenSaver -framework WebKit -framework Network "$here"/*.swift
done
lipo -create "$tmp/CanardProbe-arm64" "$tmp/CanardProbe-x86_64" -output "$out/Contents/MacOS/CanardProbe"
rm -rf "$tmp"
codesign --force --sign - "$out" >/dev/null
echo "derlendi: ${out:A} (ana sınıf: $principal)"
if [[ "${1:-}" == "--install" ]]; then
  dest="$HOME/Library/Screen Savers"
  mkdir -p "$dest"
  rm -rf "$dest/CanardProbe.saver"
  cp -R "$out" "$dest/"
  killall legacyScreenSaver 2>/dev/null || true
  echo "kuruldu: $dest/CanardProbe.saver"
fi
