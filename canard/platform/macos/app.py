"""Uygulama düzeyi macOS yardımcıları: menü çubuğu uygulaması, oturum açılışı (LaunchAgent → `open -a Canard.app`,
bkz. docs/ARCHITECTURE.md), Canard.saver kurulumu, izin durumu."""

import ctypes
import os
import plistlib
import re
import shutil
import subprocess
from functools import cache
from pathlib import Path

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

from canard.i18n import t

BUNDLE_ID = "dev.canard.app"
_AGENT = Path.home() / "Library" / "LaunchAgents" / f"{BUNDLE_ID}.plist"
_INSTALLED_SAVER = Path.home() / "Library" / "Screen Savers" / "Canard.saver"
_REPO = Path(__file__).resolve().parents[3]
_SCREENSAVER_SETTINGS = "x-apple.systempreferences:com.apple.Wallpaper-Settings.extension"
_LOCK_SCREEN_SETTINGS = "x-apple.systempreferences:com.apple.Lock-Screen-Settings.extension"
_PRIVACY_SETTINGS = {
    "system_audio": "x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}


def set_accessory() -> None:
    """Dock'ta ve ⌘Tab'da görünmez, yalnız menü çubuğu (paketli uygulamada LSUIElement aynı işi görür)."""
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def bring_to_front() -> None:
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
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{BUNDLE_ID}"], capture_output=True)
        _AGENT.unlink(missing_ok=True)
        return
    bundle = app_bundle()
    if bundle is None:
        raise OSError(t("Canard.app not found (run native/macos-app/build.sh --install first)"))
    agent = {
        "Label": BUNDLE_ID,
        "ProgramArguments": ["/usr/bin/open", "-a", str(bundle)],
        "RunAtLoad": True,
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
    """~/Library/Screen Savers'a kopyalar ve legacyScreenSaver'ı kapatır (eski .saver kodunu bellekte tutuyor)."""
    package = saver_package()
    if package is None:
        raise OSError(t("Canard.saver is not built (native/macos-saver/build.sh)"))
    _INSTALLED_SAVER.parent.mkdir(parents=True, exist_ok=True)
    staging = _INSTALLED_SAVER.with_name(".Canard.saver.new")
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
    """"granted" | "denied" | "unknown" (henüz sorulmadı) | None (okunamadı). Sorumlu sürecin (Canard.app ya da
    terminal) izni; sistem sesinin genel API'si yok, belgelenmemiş TCCAccessPreflight yalnız okunur."""
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


@cache
def _tcc_libs():
    tcc = ctypes.CDLL("/System/Library/PrivateFrameworks/TCC.framework/TCC")
    tcc.TCCAccessPreflight.restype = ctypes.c_int
    tcc.TCCAccessPreflight.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    return tcc, cf


def _tcc_preflight(service: str) -> str | None:
    tcc, cf = _tcc_libs()
    name = cf.CFStringCreateWithCString(None, service.encode(), 0x08000100)
    try:
        return {0: "granted", 1: "denied", 2: "unknown"}.get(tcc.TCCAccessPreflight(name, None))
    finally:
        cf.CFRelease(name)


def start_screensaver() -> None:
    """Sistem ekran koruyucusunu başlatır; parola "hemen" isteniyorsa oturumu da kilitler."""
    subprocess.Popen(["open", "-a", "ScreenSaverEngine"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def screen_lock_delay() -> int | None:
    """Ekran koruyucudan kaç sn sonra parola istenir: 0 = hemen, None = istenmez ya da okunamadı."""
    try:
        result = subprocess.run(["sysadminctl", "-screenLock", "status"], capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stderr + result.stdout
    if "is immediate" in text:
        return 0
    match = re.search(r"delay is (\d+) seconds", text)
    return int(match.group(1)) if match else None
