"""Faz 3a spike — macOS ekran koruyucu bildirimleri ve ekran koruyucu üstünde pencere denemesi.

Çalıştır:
    uv run python tools/screensaver-probe/probe.py            # yalnız bildirimleri logla
    uv run python tools/screensaver-probe/probe.py --overlay  # + ekran koruyucu başlayınca test penceresi

Başka bir terminalden ekran koruyucuyu başlat:  sleep 3; open -a ScreenSaverEngine
Ctrl+C ile çık.

Sorular: (1) com.apple.screensaver.didstart/willstop/didstop hâlâ geliyor mu, sırası ne?
(2) Ekran koruyucu seviyesinin üstündeki bir pencere görünür mü — özellikle kilit "hemen" iken?
Güvenlik: test penceresi ekran koruyucu dururken, her fare/tuş olayında ve en geç 20 sn sonra kapanır;
kilit ekranındaki parola alanını örtemez.
"""

import argparse
import signal
import sys
import time

import objc
from AppKit import (
    NSDistributedNotificationCenter,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowOcclusionStateVisible,
    NSWorkspace,
)
from Foundation import NSObject
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from Quartz import CGWindowLevelForKey, kCGScreenSaverWindowLevelKey

_T0 = time.monotonic()
_SAFETY_SECONDS = 20

DISTRIBUTED = [
    "com.apple.screensaver.didstart",
    "com.apple.screensaver.willstop",
    "com.apple.screensaver.didstop",
    "com.apple.screenIsLocked",
    "com.apple.screenIsUnlocked",
]
WORKSPACE = [
    "NSWorkspaceScreensDidSleepNotification",
    "NSWorkspaceScreensDidWakeNotification",
    "NSWorkspaceSessionDidResignActiveNotification",
    "NSWorkspaceSessionDidBecomeActiveNotification",
]


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:7.2f}s  {message}", flush=True)


class Overlay(QWidget):
    def __init__(self, screen, on_close):
        super().__init__(None, Qt.WindowType.FramelessWindowHint)
        self._on_close = on_close
        self._screen_name = screen.name()
        self.setWindowTitle(f"Canard overlay probe — {screen.name()}")
        self.setGeometry(screen.geometry())
        self.setMouseTracking(True)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def _tick(self):
        self._phase += 0.016
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(6, 7, 13))
        x = (self._phase * 400) % w
        p.fillRect(QRectF(x, 0, 60, h), QColor(255, 80, 160))
        p.setPen(QColor(240, 240, 255))
        p.setFont(QFont("Helvetica", 28))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                   f"Canard overlay testi — {self._screen_name}\n"
                   f"{time.strftime('%H:%M:%S')}\n\nfare/tuş ile ya da {_SAFETY_SECONDS} sn sonra kapanır")

    def mouseMoveEvent(self, _event):
        # Pencere imlecin altında açılınca gelebilecek sahte hareket testi bozmasın.
        if self._phase > 1.5:
            self._on_close("fare hareketi")

    def mousePressEvent(self, _event):
        self._on_close("fare tıklaması")

    def keyPressEvent(self, _event):
        self._on_close("tuş")

    def ns_window(self):
        return objc.objc_object(c_void_p=int(self.winId())).window()


class Probe(NSObject):
    def initWithOverlay_(self, overlay: bool):
        self = objc.super(Probe, self).init()
        if self is None:
            return None
        self.overlay_enabled = overlay
        self.overlays = []
        self.safety = None
        return self

    def distributed_(self, note):
        name = str(note.name())
        log(f"[dağıtık] {name}")
        if not self.overlay_enabled:
            return
        if name == "com.apple.screensaver.didstart":
            self.show_overlays()
        elif name in ("com.apple.screensaver.willstop", "com.apple.screensaver.didstop"):
            self.hide_overlays(name.rsplit(".", 1)[-1])

    def workspace_(self, note):
        log(f"[workspace] {note.name()}")

    def show_overlays(self):
        if self.overlays:
            return
        level = CGWindowLevelForKey(kCGScreenSaverWindowLevelKey) + 1
        for screen in QGuiApplication.screens():
            overlay = Overlay(screen, self.hide_overlays)
            overlay.show()
            window = overlay.ns_window()
            window.setLevel_(level)
            window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | NSWindowCollectionBehaviorFullScreenAuxiliary
                                          | NSWindowCollectionBehaviorStationary)
            window.orderFrontRegardless()
            self.overlays.append(overlay)
            log(f"overlay açıldı: {screen.name()} seviye={level}")
        QTimer.singleShot(1000, self.report_visibility)
        QTimer.singleShot(5000, self.report_visibility)
        self.safety = QTimer()
        self.safety.setSingleShot(True)
        self.safety.timeout.connect(lambda: self.hide_overlays(f"{_SAFETY_SECONDS} sn güvenlik süresi"))
        self.safety.start(_SAFETY_SECONDS * 1000)

    def report_visibility(self):
        for overlay in self.overlays:
            window = overlay.ns_window()
            visible = bool(window.occlusionState() & NSWindowOcclusionStateVisible)
            log(f"overlay görünürlük ({overlay._screen_name}): {'GÖRÜNÜR' if visible else 'ÖRTÜLÜ'} "
                f"(occlusionState={window.occlusionState()}, isOnActiveSpace={window.isOnActiveSpace()})")

    def hide_overlays(self, reason="?"):
        if not self.overlays:
            return
        if self.safety is not None:
            self.safety.stop()
        for overlay in self.overlays:
            overlay.close()
        self.overlays = []
        log(f"overlay kapandı ({reason})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overlay", action="store_true", help="ekran koruyucu başlayınca test penceresi aç")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)

    probe = Probe.alloc().initWithOverlay_(args.overlay)
    center = NSDistributedNotificationCenter.defaultCenter()
    for name in DISTRIBUTED:
        center.addObserver_selector_name_object_(probe, "distributed:", name, None)
    ws_center = NSWorkspace.sharedWorkspace().notificationCenter()
    for name in WORKSPACE:
        ws_center.addObserver_selector_name_object_(probe, "workspace:", name, None)

    log(f"dinleniyor{' (+ overlay)' if args.overlay else ''} — başka terminalden: sleep 3; open -a ScreenSaverEngine")
    code = app.exec()
    probe.hide_overlays("çıkış")
    return code


if __name__ == "__main__":
    sys.exit(main())
