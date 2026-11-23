"""WebKit arka ucu (WKWebView, PyObjC): yalnız macOS 14+.

WKWebView Qt penceresine yerel görünüm olarak gömülür: düz bir NSView kabı `QWindow.fromWinId` +
`QWidget.createWindowContainer` ile Qt düzenine girer, WKWebView kabın alt görünümüdür. Duraklatma WKWebView'ı gizler
(Qt kabın görünürlüğünü kendisi yönetir, bu yüzden kabın değil alt görünümün durumu kullanılır): sayfa
`visibilityState = hidden` olur, rAF durur, medya askıya alınır. Ölçümler ve gerekçe: PLAN.md "macOS çift motor".
"""

import json
import logging
import time
import weakref
from functools import cache, partial
from pathlib import Path

import objc
import Quartz
import shiboken6
import WebKit
from AppKit import NSMakeRect, NSView, NSViewHeightSizable, NSViewWidthSizable, NSWindowDidBecomeKeyNotification
from Foundation import NSURL, NSBundle, NSNotificationCenter, NSObject, NSProcessInfo, NSURLRequest, NSUUID
from PySide6.QtCore import Qt
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QWidget

from canard.web import LABELS, WEBKIT
from canard.web.base import WebEngine, WebHost, notify_media_request

log = logging.getLogger("canard.web")

# Canard'ın kalıcı WebKit deposu (localStorage, IndexedDB, çerezler, önbellek). Sabit kalmalı: değişirse içerik
# verileri yeni, boş bir depoda başlar. Konum WebKit'in seçimidir: ~/Library/WebKit/<süreç>/WebsiteDataStore/<kimlik>.
DATA_STORE_ID = "C4A7D0E2-5B13-4F6A-9E28-7D1B3C6F0A54"
_HERE = Path(__file__).resolve().parent
_CONSOLE_LEVELS = {"error": logging.WARNING, "warn": logging.INFO}
# Yüklenirken başka yüklemeyle kesilen gezinme hata değildir.
_IGNORED_ERRORS = {("NSURLErrorDomain", -999), ("WebKitErrorDomain", 102), ("WebKitErrorDomain", 204)}
# İçerik süreci bu sürede bu kadar kez çökerse yeniden yükleme bırakılır, hata ekranı gösterilir.
_CRASH_WINDOW_S = 60
_CRASH_LIMIT = 3


@cache
def _script(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8")


class WebKitEngine(WebEngine):
    name = WEBKIT
    label = LABELS[WEBKIT]

    def __init__(self):
        store_class = WebKit.WKWebsiteDataStore
        if store_class.respondsToSelector_(b"dataStoreForIdentifier:"):
            self.data_store = store_class.dataStoreForIdentifier_(NSUUID.alloc().initWithUUIDString_(DATA_STORE_ID))
        else:  # macOS 13 ve öncesi: tek varsayılan depo
            log.warning("WebKit data store per identifier needs macOS 14; using the default store")
            self.data_store = store_class.defaultDataStore()
        self.scripts = [_script("console.js")]

    def version(self) -> str:
        bundle = NSBundle.bundleWithIdentifier_("com.apple.WebKit")
        version = bundle.objectForInfoDictionaryKey_("CFBundleVersion") if bundle is not None else None
        return f"WebKit {version}" if version else "WebKit"

    def data_location(self) -> Path:
        owner = NSBundle.mainBundle().bundleIdentifier() or NSProcessInfo.processInfo().processName()
        return Path.home() / "Library" / "WebKit" / str(owner) / "WebsiteDataStore" / DATA_STORE_ID.lower()

    def set_scripts(self, bridge: str, system_audio: bool) -> None:
        self.scripts = [_script("console.js"), bridge]
        if system_audio:
            self.scripts.append(_script("system_audio.js"))

    def create_host(self, boot_script: str | None = None, audio_hub=None) -> "WebKitHost":
        return WebKitHost(self, boot_script=boot_script, audio_hub=audio_hub)


def _with_host(delegate, action) -> None:
    """ObjC geri çağrısından kaçan Python hatası süreci düşürür; burada loglanır. Sayfa silinmişse çağrı yok sayılır."""
    host = delegate._host()
    if host is None or not shiboken6.isValid(host):
        return
    try:
        action(host)
    except Exception:
        log.exception("WebKit callback failed")


def _reset_audio(host) -> None:
    if host.page_audio is not None:
        host.page_audio.reset()


def _url(web_view) -> str:
    url = web_view.URL()
    return str(url.absoluteString()) if url is not None else ""


# WKUIDelegate protokol listesinde değil: PyObjC protokol doğrulaması genişletilmiş blok imzasını kabul etmiyor;
# WebKit yalnız respondsToSelector ile bakar.
class CanardWebKitDelegate(NSObject, protocols=[objc.protocolNamed("WKNavigationDelegate"),
                                                objc.protocolNamed("WKScriptMessageHandler")]):
    def initWithHost_(self, host):
        self = objc.super(CanardWebKitDelegate, self).init()
        if self is None:
            return None
        self._host = weakref.ref(host)
        return self

    def webView_didStartProvisionalNavigation_(self, _web_view, _navigation):
        _with_host(self, _reset_audio)

    def webView_didFinishNavigation_(self, web_view, _navigation):
        _with_host(self, lambda host: host._loaded(True, _url(web_view)))

    def webView_didFailProvisionalNavigation_withError_(self, _web_view, _navigation, error):
        _with_host(self, lambda host: host._navigation_failed(error))

    def webView_didFailNavigation_withError_(self, _web_view, _navigation, error):
        _with_host(self, lambda host: host._navigation_failed(error))

    def webViewWebContentProcessDidTerminate_(self, _web_view):
        _with_host(self, lambda host: host._process_terminated())

    # Mikrofon/kamera: Chromium'daki politika gibi sormadan izin verilir (macOS izni yine Canard'a sorulur). PyObjC
    # 13'te bu yöntemin blok imzası yok ("cannot call block without a signature"): genişletilmiş tür kodlamasıyla.
    @objc.typedSelector(b"v@:@@@q@?<v@?q>")
    def webView_requestMediaCapturePermissionForOrigin_initiatedByFrame_type_decisionHandler_(
            self, _web_view, origin, _frame, capture_type, decision_handler):
        try:
            if capture_type in (WebKit.WKMediaCaptureTypeMicrophone, WebKit.WKMediaCaptureTypeCameraAndMicrophone):
                notify_media_request("microphone")
            log.debug("permission granted: media capture type %d (%s)", capture_type, origin.host())
        except Exception:
            log.exception("media capture permission callback failed")
        decision_handler(WebKit.WKPermissionDecisionGrant)

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        _with_host(self, lambda host: host._message(str(message.name()), message.body()))


class CanardWebKitContainer(NSView):
    """Qt'nin yerel pencere kabı. Klavye sayfaya gitsin diye ilk yanıtlayıcı WKWebView olmalı; Qt ise pencere
    etkinleşince odağı kendi görünümüne (ya da bu kaba) verir. Pencere anahtar olunca ve kap ilk yanıtlayıcı olunca
    odak WKWebView'a aktarılır (iç içe makeFirstResponder olmasın diye bir sonraki döngüde, Qt'den sonra)."""

    def acceptsFirstResponder(self):
        return True

    def becomeFirstResponder(self):
        self._schedule_focus()
        return True

    def viewDidMoveToWindow(self):
        center = NSNotificationCenter.defaultCenter()
        center.removeObserver_name_object_(self, NSWindowDidBecomeKeyNotification, None)
        if self.window() is not None:
            center.addObserver_selector_name_object_(self, b"windowDidBecomeKey:", NSWindowDidBecomeKeyNotification,
                                                     self.window())

    def windowDidBecomeKey_(self, _notification):
        self._schedule_focus()

    def _schedule_focus(self):
        self.performSelector_withObject_afterDelay_(b"focusWebView:", None, 0)

    def focusWebView_(self, _sender):
        window, subviews = self.window(), self.subviews()
        if window is None or not subviews or subviews[0].isHiddenOrHasHiddenAncestor():
            return
        web_view, responder = subviews[0], window.firstResponder()
        if isinstance(responder, NSView) and responder.isDescendantOf_(web_view):
            return
        window.makeFirstResponder_(web_view)


def _teardown(web_view, controller, delegate, audio_hub, page_audio, *_):
    # Sayfa (QWidget) silinirken: WebKit'in güçlü tuttuğu ileti işleyicileri bırakılır, sayfa kapanır.
    try:
        web_view.stopLoading()
        web_view.setNavigationDelegate_(None)
        web_view.setUIDelegate_(None)
        controller.removeAllScriptMessageHandlers()
        controller.removeAllUserScripts()
        container = web_view.superview()
        web_view.removeFromSuperview()
        if container is not None:
            NSNotificationCenter.defaultCenter().removeObserver_(container)
    except Exception:
        log.exception("WebKit page teardown failed")
    if audio_hub is not None and page_audio is not None:
        audio_hub.forget(page_audio)


class WebKitHost(WebHost):
    def __init__(self, engine: WebKitEngine, boot_script: str | None = None, audio_hub=None, parent=None):
        super().__init__(boot_script, parent)
        self._engine = engine
        self._crashes: list[float] = []

        config = WebKit.WKWebViewConfiguration.alloc().init()
        config.setWebsiteDataStore_(engine.data_store)
        config.setMediaTypesRequiringUserActionForPlayback_(WebKit.WKAudiovisualMediaTypeNone)
        preferences = config.preferences()
        if preferences.respondsToSelector_(b"setElementFullscreenEnabled:"):
            preferences.setElementFullscreenEnabled_(True)
        self._delegate = CanardWebKitDelegate.alloc().initWithHost_(self)
        self._window_capture = True  # pencere sunucusundan kare alınabiliyor (bkz. grab_frame)
        controller = self._controller = config.userContentController()
        controller.addScriptMessageHandler_name_(self._delegate, "canardConsole")
        if audio_hub is not None:
            controller.addScriptMessageHandler_name_(self._delegate, "canardAudio")

        container = self._container = CanardWebKitContainer.alloc().initWithFrame_(NSMakeRect(0, 0, 640, 480))
        container.setAutoresizesSubviews_(True)
        container.setWantsLayer_(True)
        container.layer().setBackgroundColor_(Quartz.CGColorGetConstantColor(Quartz.kCGColorBlack))
        web_view = self._web_view = WebKit.WKWebView.alloc().initWithFrame_configuration_(container.bounds(), config)
        web_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        web_view.setNavigationDelegate_(self._delegate)
        web_view.setUIDelegate_(self._delegate)
        try:
            web_view.setValue_forKey_(False, "drawsBackground")  # ilk çizimden önce beyaz parlama olmasın
        except Exception:
            log.debug("WKWebView drawsBackground could not be turned off")
        if web_view.respondsToSelector_(b"setInspectable:"):
            web_view.setInspectable_(True)  # Safari › Geliştir menüsünden incelenebilir (yalnız bu Mac'te)
        # Örtülünce duraklatmaya Canard karar verir (Chromium'daki gibi). WebKit'in kendi algılaması açıkken kilit
        # ekranı sayfayı gizli yapıyor ve Canard.saver bağlanıp kapatsa da WebKit görünürlüğü yeniden hesaplamıyor
        # (sistem logunda görüldü). Belgelenmemiş ayar; yoksa kilitliyken yayın donabilir.
        if web_view.respondsToSelector_(b"_setWindowOcclusionDetectionEnabled:"):
            web_view._setWindowOcclusionDetectionEnabled_(False)
        else:
            log.warning("WebKit occlusion detection cannot be turned off; the screensaver stream may freeze")
        container.addSubview_(web_view)

        view = QWidget.createWindowContainer(QWindow.fromWinId(objc.pyobjc_id(container)), self)
        view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._set_view(view)

        page_audio = None
        if audio_hub is not None:
            page_audio = self.page_audio = audio_hub.page()
            page_audio.chunk.connect(self._push_audio)
            page_audio.formatChanged.connect(self._audio_format_changed)
        self.destroyed.connect(partial(_teardown, web_view, controller, self._delegate, audio_hub, page_audio))

    def _load(self, url: str) -> None:
        controller = self._controller
        controller.removeAllUserScripts()
        for source in self._engine.scripts:
            controller.addUserScript_(WebKit.WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(
                source, WebKit.WKUserScriptInjectionTimeAtDocumentStart, True))
        if self.page_audio is not None:
            self.page_audio.reset()
        ns_url = NSURL.URLWithString_(url)
        if ns_url is None:
            log.error("invalid URL: %s", url)
            self._show_error()
            return
        self._web_view.loadRequest_(NSURLRequest.requestWithURL_(ns_url))

    def run_js(self, source: str) -> None:
        self._web_view.evaluateJavaScript_completionHandler_(source, None)

    def _load_blank(self) -> None:
        self._web_view.loadHTMLString_baseURL_("", None)

    def _apply_suspended(self, suspended: bool) -> None:
        log.debug("page %s", "suspended (hidden + media suspended)" if suspended else "active")
        web_view = self._web_view
        web_view.setHidden_(suspended)
        if web_view.respondsToSelector_(b"setAllMediaPlaybackSuspended:completionHandler:"):
            web_view.setAllMediaPlaybackSuspended_completionHandler_(suspended, None)

    def grab_frame(self, width: int, height: int, callback) -> None:
        # Pencere sunucusunun bu pencere için tuttuğu son kare: takeSnapshot sayfayı yeniden çizdiriyor, ağır WebGL
        # içerikte yavaş (3420×2224 fire ~70 ms/kare, bu yol ~2 ms; kilitliyken de güncel — ölçüldü). Alınamazsa
        # takeSnapshot.
        image = self._window_image() if self._window_capture else None
        if image is not None:
            callback(image)
            return
        self._snapshot(width, callback)

    def _window_image(self):
        web_view = self._web_view
        window = web_view.window()
        if window is None:
            return None
        image = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow, window.windowNumber(),
            Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageBestResolution)
        if image is None:
            self._window_capture = False
            log.warning("window capture unavailable, using WebKit snapshots for the screensaver stream (slower)")
            return None
        # Pencerede web görünümünden başka alan varsa (ör. etkileşimli moddaki üst boşluk) yalnız görünüm kesilir.
        scale = window.backingScaleFactor()
        rect = web_view.convertRect_toView_(web_view.bounds(), None)
        height = window.contentView().bounds().size.height
        crop = Quartz.CGRectMake(round(rect.origin.x * scale),
                                 round((height - rect.origin.y - rect.size.height) * scale),
                                 round(rect.size.width * scale), round(rect.size.height * scale))
        if (crop.size.width, crop.size.height) != (Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)):
            image = Quartz.CGImageCreateWithImageInRect(image, crop)
        return image

    def _snapshot(self, width: int, callback) -> None:
        config = WebKit.WKSnapshotConfiguration.alloc().init()
        config.setAfterScreenUpdates_(False)  # son çizilen kare yeter; beklemek gecikme ekler
        window = self._web_view.window()
        scale = window.backingScaleFactor() if window is not None else 2.0
        config.setSnapshotWidth_(width / scale)  # nokta cinsinden; görüntü piksel genişliği = nokta × ölçek

        def done(image, error):
            frame = None
            try:
                if image is not None:
                    frame, _rect = image.CGImageForProposedRect_context_hints_(None, None, None)
                else:
                    log.debug("snapshot failed: %s", error)
            except Exception:
                log.exception("snapshot could not be converted")
            try:
                callback(frame)
            except Exception:
                log.exception("snapshot callback failed")

        self._web_view.takeSnapshotWithConfiguration_completionHandler_(config, done)

    def _navigation_failed(self, error) -> None:
        if (str(error.domain()), error.code()) in _IGNORED_ERRORS:
            return
        log.error("navigation failed: %s (%s %d)", error.localizedDescription(), error.domain(), error.code())
        self._loaded(False)

    def _process_terminated(self) -> None:
        now = time.monotonic()
        self._crashes = [at for at in self._crashes if now - at < _CRASH_WINDOW_S] + [now]
        if len(self._crashes) >= _CRASH_LIMIT:
            log.error("page process terminated %d times in %d s, giving up (%s)", len(self._crashes),
                      _CRASH_WINDOW_S, self._current_url)
            self._show_error()
            return
        log.error("page process terminated, reloading (%s)", self._current_url)
        self.retry()

    def _message(self, name: str, body) -> None:
        if name == "canardConsole":
            text = str(body["text"])
            level = logging.INFO if text.startswith("[canard") else _CONSOLE_LEVELS.get(str(body["level"]),
                                                                                        logging.DEBUG)
            log.log(level, "console: %s", text)
        elif name == "canardAudio" and self.page_audio is not None:
            command = str(body["cmd"])
            if command == "start":
                info = self.page_audio.start()
                self.run_js("window.__canardAudio.started(%s)" % json.dumps(info))
            elif command == "stop":
                self.page_audio.stop()
            elif command == "stats":
                stats = body["stats"]
                log.debug("system audio in page: %s", " ".join(f"{key}={stats[key]}" for key in sorted(stats)))

    def _push_audio(self, chunk: str) -> None:
        self.run_js("window.__canardAudio&&window.__canardAudio.push('%s')" % chunk)

    def _audio_format_changed(self, info) -> None:
        self.run_js("window.__canardAudio&&window.__canardAudio.format(%s)" % json.dumps(dict(info)))
