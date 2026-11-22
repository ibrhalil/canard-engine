#!/bin/zsh
# Canard.app geliştirici uygulamasını Xcode olmadan (Command Line Tools + swiftc) derler: Swift başlatıcı + Info.plist
# (izin açıklamaları, LSUIElement, depo yolu) + içinde Canard.saver. Python ve Qt depodaki .venv'den çalışır; Python'u
# gömen dağıtılabilir paket Faz 6'da.
#   native/macos-app/build.sh             → build/Canard.app
#   native/macos-app/build.sh --install   → derle ve ~/Applications/Canard.app'e kur; Canard.saver kuruluysa onu da
#                                           günceller ve ekran koruyucu sürecini yeniden başlatır (eski kodu bellekte tutar)
# İmza: CANARD_SIGN_IDENTITY="<kod imzalama sertifikası adı>", verilmezse anahtar zincirinde "Canard Dev" varsa o
# (kendinden imzalı; izin kararları yeniden derlemede korunur, bkz. docs/KULLANIM.md), o da yoksa ad-hoc (her yeniden
# derlemeden sonra macOS izinleri tekrar sorabilir).
set -euo pipefail
here=${0:A:h}
root=${here:h:h}
out="$root/build/Canard.app"
identity=${CANARD_SIGN_IDENTITY:-}
if [[ -z "$identity" ]] && security find-certificate -c "Canard Dev" >/dev/null 2>&1; then
  identity="Canard Dev"
fi
identity=${identity:--}
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/canard/__init__.py")

"$root/native/macos-saver/build.sh" >/dev/null

rm -rf "$out"
mkdir -p "$out/Contents/MacOS" "$out/Contents/Resources"
cat > "$out/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleDevelopmentRegion</key><string>tr</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleExecutable</key><string>Canard</string>
  <key>CFBundleIdentifier</key><string>dev.canard.app</string>
  <key>CFBundleName</key><string>Canard</string>
  <key>CFBundleDisplayName</key><string>Canard</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>CFBundleIconFile</key><string>Canard</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>GPL-3.0</string>
  <key>NSMicrophoneUsageDescription</key><string>Canard, gösterdiği web içeriği mikrofon istediğinde (ör. ses görselleştirici) sesi içeriğe iletir.</string>
  <key>NSAudioCaptureUsageDescription</key><string>Canard, bilgisayarda çalan sesi duvar kağıdı ve ekran koruyucu içeriğine (ör. ses görselleştirici) iletir.</string>
  <key>NSCameraUsageDescription</key><string>Canard, gösterdiği web içeriği kamera istediğinde görüntüyü içeriğe iletir.</string>
  <key>CanardRoot</key><string>$root</string>
</dict></plist>
PLIST

tmp=$(mktemp -d)
for arch in arm64 x86_64; do
  xcrun swiftc -O -target "$arch-apple-macos14.0" -o "$tmp/Canard-$arch" "$here/CanardLauncher.swift"
done
lipo -create "$tmp/Canard-arm64" "$tmp/Canard-x86_64" -output "$out/Contents/MacOS/Canard"
rm -rf "$tmp"
"$root/.venv/bin/python" "$root/native/render_icon.py" icns "$out/Contents/Resources/Canard.icns"

ditto "$root/build/Canard.saver" "$out/Contents/Resources/Canard.saver"
sign() {
  # codesign bilgi satırlarını ("replacing existing signature") stderr'e yazar: yalnız hata olursa gösterilir.
  local output
  if ! output=$(codesign --force --sign "$identity" "$1" 2>&1); then
    print -u2 -- "$output"
    print -u2 -- "imzalanamadı: $1 (kimlik: $identity)"
    exit 1
  fi
}
sign "$out/Contents/Resources/Canard.saver"
sign "$out"
echo "derlendi: $out (imza: ${identity/#-/ad-hoc})"

if [[ "${1:-}" == "--install" ]]; then
  target="$HOME/Applications/Canard.app"
  mkdir -p "$HOME/Applications"
  rm -rf "$target"
  ditto "$out" "$target"
  echo "kuruldu: $target  (açmak için: open ~/Applications/Canard.app)"
  saver="$HOME/Library/Screen Savers/Canard.saver"
  if [[ -d "$saver" ]]; then
    rm -rf "$saver"
    ditto "$out/Contents/Resources/Canard.saver" "$saver"
    killall legacyScreenSaver 2>/dev/null || true
    echo "güncellendi: $saver (ekran koruyucu süreci yeniden başlatıldı)"
  fi
fi
