"""WebKit duvar kağıdı denemesi: Canard'ın macOS duvar kağıdı penceresi, QtWebEngine yerine PyObjC + WKWebView ile.

Yanıtlanacak sorular (PLAN.md, motor kararı):
  1. PyObjC + WKWebView, Swift + WKWebView (tools/engine-bench/WebKitBench) kadar verimli mi?
  2. WebKit, masaüstü seviyesindeki duvar kağıdı penceresinde çizimi kendiliğinden durduruyor mu?
  3. Sistem sesi WebKit'e aktarılabiliyor mu (MediaStreamTrackGenerator olmadan, system_audio_webaudio.js)?

tools/engine-bench/run.py ile aynı satır protokolünü konuşur (ready · fps=… · capture yok), ek olarak tanı
satırları yazar: `event=…` (örtülme, takılma, içerik süreci) ve `audio=…` (köprü istatistiği). Örnekler:
  uv run python tools/webkit-wallpaper/spike.py --log build/webkit-wallpaper.log   # duvar kağıdı + sistem sesi
  uv run python tools/webkit-wallpaper/spike.py --window fullscreen   # kıyas penceresi (Esc ile çıkılır)
  uv run python tools/engine-bench/run.py wkpy --wallpaper --boot system --label …
Duvar kağıdı tıklama/klavye almaz; ⌃⌥⌘K etkileşimli modu açar/kapatır (Canard'daki gibi simgelerin üstüne çıkar,
menü çubuğu açık kalır). mscillus'ta mod ve ayar değişiklikleri `event=mode/settings` olarak loglanır, takılma
satırlarıyla eşleştirilebilsin diye. Ctrl+C ya da SIGTERM ile çıkılır (Canard kapalı olmalı: aynı kısayol). Profil Canard'ınkinden ve Swift kıyasınınkinden
ayrıdır (sabit kimlikli WKWebsiteDataStore); içerik ayarları çalıştırmalar arasında korunur.
"""

import argparse
import base64
import json
import re
import signal
import sys
import threading
import time
from pathlib import Path

import objc
import WebKit
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSColor,
    NSFloatingWindowLevel,
    NSMakeRect,
    NSNotificationCenter,
    NSRunLoopCommonModes,
    NSScreen,
    NSTimer,
    NSViewHeightSizable,
    NSViewWidthSizable,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowDidChangeOcclusionStateNotification,
    NSWindowOcclusionStateVisible,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSURL, NSObject, NSRunLoop, NSURLRequest, NSUUID
from PyObjCTools import AppHelper
from Quartz import CGWindowLevelForKey, kCGDesktopIconWindowLevelKey

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from canard.platform.macos.audio_tap import SystemAudioTap  # noqa: E402
from canard.platform.macos.hotkeys import DEFAULT_BINDINGS, HotkeyRegistry  # noqa: E402

DEFAULT_URL = "https://mscillus.ibrhalil.com/"
BOOTS = {"mic": ROOT / "tools" / "mscillus-boot-mic.js", "system": ROOT / "tools" / "mscillus-boot-system.js"}
DATA_STORE_ID = "6E1F3C2A-9B7D-4C1E-8A55-0C4A5E7D2B91"  # deneme profili; Canard ve WebKitBench'ten ayrı
STALL_FPS = 30  # görünürken bunun altı "takılma" sayılır
STATE_JS = ("JSON.stringify({m: sessionStorage.getItem('mscillus.lastMode'),"
            " s: localStorage.getItem('mscillus.settings.v1')})")
_log_file = None


def emit(line: str) -> None:
    if _log_file is not None:
        _log_file.write(line + "\n")
        _log_file.flush()
    try:
        print(line, flush=True)
    except (BrokenPipeError, OSError):
        # Çıktı borusu kapandı (ör. `| tee` Ctrl+C ile önce öldü): ObjC geri çağrısında hata çökme penceresi açar.
        sys.stdout = open("/dev/null", "w")  # noqa: SIM115
        AppHelper.stopEventLoop()


def stamp() -> str:
    return time.strftime("%H:%M:%S")


class AudioBridge:
    """Core Audio tap → (ana iş parçacığı, 20 ms) → sayfa. Tap geri çağrısı gerçek zamanlı: yalnız tampona ekler."""

    def __init__(self, spike: "Spike"):
        self._spike = spike
        self._tap: SystemAudioTap | None = None
        self._lock = threading.Lock()
        self._pending = bytearray()
        self._timer = None
        self.sent = 0

    def start(self) -> dict:
        try:
            if self._tap is None:
                self._tap = SystemAudioTap(self._on_chunk)
            fmt = self._tap.start()
        except Exception as error:
            emit(f"event=audio-error {stamp()} {error!r}")
            return {"ok": False, "error": str(error)}
        if self._timer is None:
            self._timer = NSTimer.timerWithTimeInterval_repeats_block_(0.02, True, lambda _t: self._drain())
            NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, NSRunLoopCommonModes)
        emit(f"event=audio-start {stamp()} rate={fmt.sample_rate:.0f} channels={fmt.channels} "
             f"interleaved={fmt.interleaved}")
        return {"ok": True, "sampleRate": fmt.sample_rate, "channels": fmt.channels, "interleaved": fmt.interleaved}

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._tap is not None:
            self._tap.stop()
            emit(f"event=audio-stop {stamp()} chunks={self.sent}")
        with self._lock:
            self._pending.clear()

    def _on_chunk(self, data: bytes) -> None:
        with self._lock:
            self._pending += data

    def _drain(self) -> None:
        with self._lock:
            if not self._pending:
                return
            data = bytes(self._pending)
            self._pending.clear()
        self.sent += 1
        self._spike.js(f"window.__canardAudio&&window.__canardAudio.push('{base64.b64encode(data).decode('ascii')}')")


# WKUIDelegate protokol listesinde değil: PyObjC protokol doğrulaması genişletilmiş blok imzasını kabul etmiyor;
# WebKit yalnız respondsToSelector ile bakar.
class Delegate(NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate"),
                                    objc.protocolNamed("WKScriptMessageHandler")]):
    def webView_didFinishNavigation_(self, _web_view, _navigation):
        self.spike.loaded()

    def webView_didFailProvisionalNavigation_withError_(self, _web_view, _navigation, error):
        emit(f"event=load-error {stamp()} {error.localizedDescription()}")

    def webViewWebContentProcessDidTerminate_(self, web_view):
        emit(f"event=content-process-terminated {stamp()} → reload")
        web_view.reload()

    # Mikrofon/kamera: Canard'ın politikası gibi sormadan izin ver (WebKitBench ile aynı). PyObjC 13'te bu yöntemin
    # blok imzası yok ("cannot call block without a signature"): genişletilmiş tür kodlamasıyla verilir.
    @objc.typedSelector(b"v@:@@@q@?<v@?q>")
    def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
            self, _web_view, _origin, _frame, _type, decision_handler):
        decision_handler(WebKit.WKPermissionDecisionGrant)

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        try:
            self.spike.message(str(message.name()), message.body())
        except Exception as error:  # ObjC geri çağrısından kaçan hata süreci çökertir
            emit(f"event=error {stamp()} {error!r}")


class KeyWindow(NSWindow):
    """Klavye alabilen kenarsız pencere. Kıyas (tam ekran) penceresinde Esc çıkar; duvar kağıdında Esc sayfaya gider."""

    escape_quits = False

    def canBecomeKeyWindow(self):
        return True

    def keyDown_(self, event):
        if self.escape_quits and event.keyCode() == 53:
            AppHelper.stopEventLoop()
        else:
            objc.super(KeyWindow, self).keyDown_(event)


class Spike:
    def __init__(self, args):
        self.args = args
        self.ready = False
        self.visible = True
        self.audio = AudioBridge(self)
        self.interactive = False
        self.state = {"m": None, "s": None}
        self.delegate = Delegate.alloc().init()
        self.delegate.spike = self

        self.screen = NSScreen.screens()[0]  # menü çubuklu ekran (mainScreen odaklı pencerenin ekranıdır)
        frame = self.screen.frame()
        self.window = KeyWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        if args.window == "wallpaper":
            self.window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                               | NSWindowCollectionBehaviorStationary
                                               | NSWindowCollectionBehaviorIgnoresCycle)
            self._apply_wallpaper_mode()
        else:
            self.window.escape_quits = True
        self.window.setReleasedWhenClosed_(False)
        self.window.setHasShadow_(False)
        self.window.setBackgroundColor_(NSColor.blackColor())
        self.window.setTitle_("Canard WebKit denemesi")

        configuration = WebKit.WKWebViewConfiguration.alloc().init()
        configuration.setMediaTypesRequiringUserActionForPlayback_(WebKit.WKAudiovisualMediaTypeNone)
        store_id = NSUUID.alloc().initWithUUIDString_(DATA_STORE_ID)
        configuration.setWebsiteDataStore_(WebKit.WKWebsiteDataStore.dataStoreForIdentifier_(store_id))
        controller = configuration.userContentController()
        for name in ("fps_meter.js",):
            source = (ROOT / "tools" / "engine-bench" / name).read_text(encoding="utf-8")
            controller.addUserScript_(self._script(source))
        if args.prelude:  # içerik kodundan önce (ör. mscillus ayarları ve modu; bkz. tools/engine-bench/suite.sh)
            controller.addUserScript_(self._script(args.prelude.read_text(encoding="utf-8")))
        if not args.no_system_audio:
            controller.addUserScript_(self._script((HERE / "system_audio_webaudio.js").read_text(encoding="utf-8")))
        controller.addScriptMessageHandler_name_(self.delegate, "bench")
        controller.addScriptMessageHandler_name_(self.delegate, "canardAudio")

        size = frame.size
        self.web_view = WebKit.WKWebView.alloc().initWithFrame_configuration_(
            NSMakeRect(0, 0, size.width, size.height), configuration)
        self.web_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        self.web_view.setNavigationDelegate_(self.delegate)
        self.web_view.setUIDelegate_(self.delegate)
        if self.web_view.respondsToSelector_("setInspectable:"):
            self.web_view.setInspectable_(True)  # Safari › Geliştir menüsünden incelenebilir
        self.window.setContentView_(self.web_view)

        NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            NSWindowDidChangeOcclusionStateNotification, self.window, None, self._occlusion_changed)

        if args.window == "wallpaper":
            self.window.orderFrontRegardless()
            self.hotkeys = HotkeyRegistry()
            key, modifiers, label = DEFAULT_BINDINGS["toggle_interactive"]
            if not self.hotkeys.register(key, modifiers, self.toggle_interactive):
                emit(f"event=hotkey-error {stamp()} {label} kaydedilemedi (Canard açık mı?)")
        else:
            # Odak almadan en üstte (WebKitBench ile aynı): kullanıcının klavyesi kıyas sayfasına gitmesin.
            self.window.setLevel_(NSFloatingWindowLevel)
            self.window.orderFrontRegardless()
        emit(f"event=window {stamp()} mode={args.window} level={self.window.level()} "
             f"size={int(size.width)}x{int(size.height)}")
        self.web_view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(args.url)))

    def _apply_wallpaper_mode(self) -> None:
        # canard/platform/macos/wallpaper.py ile aynı: pasifte simgelerin bir altı ve tıklama yok; etkileşimlide
        # simgelerin bir üstü, menü çubuğu açık kalsın diye pencere onun altından başlar.
        frame = self.screen.frame()
        if self.interactive:
            visible = self.screen.visibleFrame()
            top_inset = (frame.origin.y + frame.size.height) - (visible.origin.y + visible.size.height)
            frame = NSMakeRect(frame.origin.x, frame.origin.y, frame.size.width, frame.size.height - top_inset)
        self.window.setFrame_display_(frame, True)
        self.window.setLevel_(CGWindowLevelForKey(kCGDesktopIconWindowLevelKey) + (1 if self.interactive else -1))
        self.window.setIgnoresMouseEvents_(not self.interactive)

    def toggle_interactive(self) -> None:
        self.interactive = not self.interactive
        self._apply_wallpaper_mode()
        app = NSApplication.sharedApplication()
        if self.interactive:
            app.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
        else:
            self.window.resignKeyWindow()
            self.window.orderFrontRegardless()
        emit(f"event=interactive {stamp()} {'on' if self.interactive else 'off'}")

    def poll_state(self) -> None:
        # mscillus'un modu (sessionStorage) ve ayarları (localStorage): değişince logla.
        self.web_view.evaluateJavaScript_completionHandler_(STATE_JS, self._state_result)

    def _state_result(self, result, _error) -> None:
        try:
            self._update_state(json.loads(str(result)) if result else None)
        except Exception as error:  # ObjC geri çağrısından kaçan hata süreci çökertir
            emit(f"event=error {stamp()} {error!r}")

    def _update_state(self, state) -> None:
        if not state:
            return
        if state.get("m") != self.state["m"]:
            emit(f"event=mode {stamp()} {state.get('m')}")
        if state.get("s") != self.state["s"] and state.get("s"):
            emit(f"event=settings {stamp()} {_settings_diff(self.state['s'], state['s'])}")
        self.state = state

    @staticmethod
    def _script(source: str):
        return WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
            source, WebKit.WKUserScriptInjectionTimeAtDocumentStart, True)

    def js(self, source: str) -> None:
        self.web_view.evaluateJavaScript_completionHandler_(source, None)

    def loaded(self) -> None:
        if self.ready:
            return
        self.ready = True
        emit("ready")
        if self.args.boot:
            self.js(self.args.boot)
        for index in range(self.args.keys):
            AppHelper.callLater(5 + index * 0.4, self.send_key, "ArrowRight")
        if self.args.window == "wallpaper":
            self.state_timer = NSTimer.timerWithTimeInterval_repeats_block_(2, True, lambda _t: self.poll_state())
            NSRunLoop.mainRunLoop().addTimer_forMode_(self.state_timer, NSRunLoopCommonModes)

    def send_key(self, key: str) -> None:
        self.js("(function(k){var o={key:k,code:k,bubbles:true,cancelable:true};"
                "document.dispatchEvent(new KeyboardEvent('keydown',o));"
                f"document.dispatchEvent(new KeyboardEvent('keyup',o));}})('{key}');")

    def message(self, name: str, body) -> None:
        if name == "bench":
            line = str(body)
            emit(line)
            match = re.match(r"fps=([\d.]+)", line)
            if match and self.visible and float(match.group(1)) < STALL_FPS:
                emit(f"event=stall {stamp()} {line}")
            return
        command = body.get("cmd") if hasattr(body, "get") else None
        if command == "start":
            info = self.audio.start()
            self.js(f"window.__canardAudio.started({json.dumps(info)})")
        elif command == "stop":
            self.audio.stop()
        elif command == "stats":
            stats = dict(body.get("stats") or {})
            emit("audio=" + " ".join(f"{k}={stats[k]}" for k in sorted(stats)) + f" sent={self.audio.sent}")

    def _occlusion_changed(self, _notification) -> None:
        self.visible = bool(self.window.occlusionState() & NSWindowOcclusionStateVisible)
        emit(f"event=occlusion {stamp()} visible={int(self.visible)}")

    def shutdown(self) -> None:
        self.audio.stop()
        if getattr(self, "hotkeys", None) is not None:
            self.hotkeys.close()


def _settings_diff(before: str | None, after: str) -> str:
    """Ayar JSON'ları arasındaki fark, kısa: üst düzey anahtarlar ve değişen mod seçenekleri."""
    try:
        old, new = json.loads(before) if before else {}, json.loads(after)
    except ValueError:
        return "okunamadı"
    if not old:
        options = new.get("modeOptions") or {}
        return (f"ilk okuma resolution={new.get('resolution')} fpsCap={new.get('fpsCap')} "
                f"fftSize={new.get('fftSize')} modeOptions={json.dumps(options, separators=(',', ':'))}")
    parts = []
    for key in sorted(set(old) | set(new)):
        if key == "modeOptions":
            old_modes, new_modes = old.get(key) or {}, new.get(key) or {}
            for mode in sorted(set(old_modes) | set(new_modes)):
                if old_modes.get(mode) != new_modes.get(mode):
                    parts.append(f"{mode}={json.dumps(new_modes.get(mode), separators=(',', ':'))}")
        elif old.get(key) != new.get(key):
            parts.append(f"{key}={json.dumps(new.get(key), separators=(',', ':'))}")
    return " ".join(parts) or "değişiklik yok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--boot", default="system", help="system (varsayılan) · mic · none · JS dosyası")
    parser.add_argument("--keys", type=int, default=0, help="yüklenince içeriğe kaç kez → gönderilsin (mscillus modu)")
    parser.add_argument("--window", choices=["wallpaper", "fullscreen"], default="wallpaper")
    parser.add_argument("--prelude", type=Path, help="belge oluşturulurken, içerikten önce çalışacak JS dosyası")
    parser.add_argument("--no-system-audio", action="store_true", help="ses köprüsünü enjekte etme")
    parser.add_argument("--log", type=Path, help="satırları bu dosyaya da yaz (tee yerine; Ctrl+C güvenli)")
    args = parser.parse_args()
    global _log_file
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        _log_file = args.log.open("a", encoding="utf-8")
        _log_file.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(sys.argv[1:])}\n")
    boot = BOOTS.get(args.boot, Path(args.boot) if args.boot != "none" else None)
    args.boot = boot.read_text(encoding="utf-8") if boot else None

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory if args.window == "wallpaper"
                             else NSApplicationActivationPolicyRegular)
    spike = Spike(args)

    signal.signal(signal.SIGTERM, lambda *_: AppHelper.stopEventLoop())
    # Python sinyal işleyicileri yalnız Python kodu çalışırken işler; olay döngüsü boştayken de uyansın.
    keepalive = NSTimer.timerWithTimeInterval_repeats_block_(0.25, True, lambda _t: None)
    NSRunLoop.mainRunLoop().addTimer_forMode_(keepalive, NSRunLoopCommonModes)
    try:
        AppHelper.runEventLoop(installInterrupt=True)
    finally:
        spike.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
