#!/bin/zsh
# Canard.saver'ı Xcode olmadan (Command Line Tools + swiftc) derler: evrensel ikili (arm64 + x86_64),
# yerel (ad-hoc) imza. Kurulum macOS'un resmi yoluyla: `open build/Canard.saver` → "yalnızca bu kullanıcı için".
#   native/macos-saver/build.sh            → build/Canard.saver
#   native/macos-saver/build.sh --open     → derle ve kurulum penceresini aç
# Kaldırmak: rm -rf ~/Library/Screen\ Savers/Canard.saver
# Küçük resim canard/resources/canard-macos.svg'den depodaki .venv'in Qt'siyle üretilir (native/render_icon.py).
set -euo pipefail
here=${0:A:h}
root=${here:h:h}
out="$root/build/Canard.saver"
rm -rf "$out"
mkdir -p "$out/Contents/MacOS" "$out/Contents/Resources"
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/canard/__init__.py")
cat > "$out/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDevelopmentRegion</key><string>en</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleSupportedPlatforms</key><array><string>MacOSX</string></array>
  <key>CFBundleExecutable</key><string>Canard</string>
  <key>CFBundleIdentifier</key><string>dev.canard.saver</string>
  <key>CFBundleName</key><string>Canard</string>
  <key>CFBundlePackageType</key><string>BNDL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>NSPrincipalClass</key><string>CanardSaverView</string>
  <key>NSHumanReadableCopyright</key><string>GPL-3.0</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
PLIST
tmp=$(mktemp -d)
for arch in arm64 x86_64; do
  xcrun swiftc -O -target "$arch-apple-macos14.0" -module-name Canard -emit-library -o "$tmp/Canard-$arch" \
    -Xlinker -bundle -framework ScreenSaver -framework AVFoundation -framework CoreMedia -framework Network \
    "$here/CanardSaverView.swift"
done
lipo -create "$tmp/Canard-arm64" "$tmp/Canard-x86_64" -output "$out/Contents/MacOS/Canard"
rm -rf "$tmp"
"$root/.venv/bin/python" "$root/native/render_icon.py" thumbnail "$out/Contents/Resources"
codesign --force --sign - "$out" >/dev/null 2>&1
echo "derlendi: ${out:A}"
if [[ "${1:-}" == "--open" ]]; then
  open "$out"
fi
