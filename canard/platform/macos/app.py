"""Uygulama düzeyi macOS yardımcıları: menü çubuğu uygulaması, oturum açılışında başlatma, Canard.saver kurulumu.

Oturum açılışı LaunchAgent iledir (docs/TASARIM-MACOS-GUNLUK.md §3.3): `open -a Canard.app` → uygulama LaunchServices
üzerinden açılır ve izinlerin (TCC) sorumlusu Canard.app olur. SMAppService imzasız/ad-hoc uygulamada belgelenmemiş.
"""

import ctypes
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

BUNDLE_ID = "dev.canard.app"
_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
_INSTALLED_SAVER = Path.home() / "Library" / "Screen Savers" / "Canard.saver"
_REPO = Path(__file__).resolve().parents[3]
# macOS 26+ ekran koruyucu Duvar Kağıdı bölmesinde (ayrı ScreenSaver-Settings uzantısı yok, macOS 27.0.1'de bakıldı).
_SCREENSAVER_SETTINGS = "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension"
_LOCK_SCREEN_SETTINGS = "x-apple.systempreferences:com.apple.Lock-Screen-Settings.extension"
# Gizlilik bölmeleri (SecurityPrivacyExtension'da bulunan bağlantılar, macOS 27.0.1'de bakıldı).
_PRIVACY_SETTINGS = {
    "system_audio": "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}


def set_accessory() -> None:
    """Dock'ta ve ⌘Tab'da görünmez, yalnız menü çubuğu (paketli uygulamada LSUIElement aynı işi görür)."""
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def bring_to_front() -> None:
    # Menü çubuğu uygulamasının iletişim kutuları öne gelsin diye.
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


def app_bundle() -> Path | None:
    """Canard.app yolu: başlatıcıdan açıldıysa onun bildirdiği, değilse kurulu olan."""
    launched = os.environ.get("CANARD_APP_BUNDLE")
    if launched and Path(launched).is_dir():
        return Path(launched)
    installed = Path.home() / "Applications" / "Canard.app"
    return installed if installed.is_dir() else None


def login_item_enabled() -> bool:
    return _AGENT.is_file()


def set_login_item(enabled: bool) -> None:
    if not enabled:
        # Yüklüyse bırakılır; plist silinince sonraki oturumda da başlamaz.
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{BUNDLE_ID}"], capture_output=True)
        _AGENT.unlink(missing_ok=True)
        return
    bundle = app_bundle()
    if bundle is None:
        raise OSError("Canard.app bulunamadı (önce native/macos-app/build.sh --install)")
    agent = {
        "Label": BUNDLE_ID,
        "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)],
        "RunAtLoad": True,
        # Sistem Ayarları › Giriş Öğeleri'nde "Canard" adıyla görünür.
        "AssociatedBundleIdentifiers": [BUNDLE_ID],
    }
    _AGENT.parent.mkdir(parents=True, exist_ok=True)
    with _AGENT.open("wb") as f:
        plistlib.dump(agent, f)


def saver_installed() -> bool:
    return _INSTALLED_SAVER.is_dir()


def saver_package() -> Path | None:
    """Kurulabilir Canard.saver: Canard.app içindeki, yoksa depodaki derleme çıktısı."""
    bundle = app_bundle()
    candidates = [bundle / "Contents" / "Resources" / "Canard.saver"] if bundle else []
    candidates.append(_REPO / "build" / "Canard.saver")
    return next((path for path in candidates if path.is_dir()), None)


def install_saver() -> None:
    """~/Library/Screen Savers'a kopyalar ve ekran koruyucu sürecini kapatır.

    legacyScreenSaver yüklediği .saver kodunu bellekte tutar (2026-11-22 sahip testi: 14:27'de açılan süreç 14:33'teki
    yeni kurulumdan sonra da eski kodu çalıştırdı). Kurulum penceresi (`open Canard.saver`) kopyalamayı onaydan sonra
    yaptığı için süreç doğru anda kapatılamaz; bu yüzden doğrudan kopyalanır. macOS süreci gerektiğinde yeniden açar.
    """
    package = saver_package()
    if package is None:
        raise OSError("Canard.saver derlenmemiş (native/macos-saver/build.sh)")
    _INSTALLED_SAVER.parent.mkdir(parents=True, exist_ok=True)
    staging = _INSTALLED_SAVER.with_name(".Canard.saver.yeni")
    shutil.rmtree(staging, ignore_errors=True)
    subprocess.run(["ditto", str(package), str(staging)], check=True, capture_output=True)
    shutil.rmtree(_INSTALLED_SAVER, ignore_errors=True)
    staging.rename(_INSTALLED_SAVER)
    subprocess.run(["killall", "legacyScreenSaver"], capture_output=True)


def open_screensaver_settings() -> None:
    subprocess.run(["open", _SCREENSAVER_SETTINGS], check=False)


def open_lock_screen_settings() -> None:
    subprocess.run(["open", _LOCK_SCREEN_SETTINGS], check=False)


def permission_status(kind: str) -> str | None:
    """Canard'ın izni: "granted" | "denied" | "unknown" (henüz sorulmadı; ilk kullanımda macOS sorar) | None (okunamadı).

    İzin, sorumlu sürece (Canard.app; terminalden çalışınca terminal) aittir. Mikrofon genel API ile (AVCaptureDevice);
    sistem sesi kaydının (kTCCServiceAudioCapture) genel API'si yok → TCC'nin belgelenmemiş TCCAccessPreflight işlevi,
    yalnız okuma (AudioCap da böyle yapar). macOS değiştirirse None döner; yakalama etkilenmez.
    """
    try:
        if kind == "microphone":
            return _microphone_status()
        if kind == "system_audio":
            return _tcc_preflight("kTCCServiceAudioCapture")
    except Exception:
        return None
    return None


def open_privacy_settings(kind: str) -> None:
    subprocess.run(["open", _PRIVACY_SETTINGS[kind]], check=False)


def _microphone_status() -> str | None:
    import objc
    from Foundation import NSBundle

    NSBundle.bundleWithPath_("/System/Library/Frameworks/AVFoundation.framework").load()
    status = objc.lookUpClass("AVCaptureDevice").authorizationStatusForMediaType_("soun")  # AVMediaTypeAudio
    return {0: "unknown", 1: "denied", 2: "denied", 3: "granted"}.get(status)  # 1 = kısıtlı (yönetim profili)


_tcc = None


def _tcc_preflight(service: str) -> str | None:
    global _tcc
    if _tcc is None:
        _tcc = ctypes.CDLL("/System/Library/PrivateFrameworks/TCC.framework/TCC")
        _tcc.TCCAccessPreflight.restype = ctypes.c_int
        _tcc.TCCAccessPreflight.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    name = cf.CFStringCreateWithCString(None, service.encode(), 0x08000100)
    try:
        return {0: "granted", 1: "denied", 2: "unknown"}.get(_tcc.TCCAccessPreflight(name, None))
    finally:
        cf.CFRelease(name)


def start_screensaver() -> None:
    """Sistem ekran koruyucusunu hemen başlatır. Kilit ekranının üstünde yalnız ekran koruyucu çalışabilir; ⌃⌘Q ya da
    Touch ID ile kilitlemek onu başlatmaz. Parola "hemen" isteniyorsa bu aynı zamanda oturumu kilitler."""
    subprocess.Popen(["open", "-a", "ScreenSaverEngine"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def screen_lock_delay() -> int | None:
    """Ekran koruyucu başladıktan kaç sn sonra parola istenir: 0 = hemen, None = istenmez ya da okunamadı.
    `sysadminctl -screenLock status` sudo istemez (~20 ms); çıktısı stderr'de: "screenLock delay is immediate"."""
    try:
        result = subprocess.run(["sysadminctl", "-screenLock", "status"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stderr + result.stdout
    if "is immediate" in text:
        return 0
    match = re.search(r"delay is (\d+) seconds", text)
    return int(match.group(1)) if match else None
