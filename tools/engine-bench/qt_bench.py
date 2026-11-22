"""Motor kıyası, Qt tarafı: Canard'ın kendi WebHost'u (QtWebEngine/Chromium) ile ana ekranı kaplayan pencere.

Doğrudan çalıştırılmaz; tools/engine-bench/run.py başlatır ve stdout satırlarını okur:
  ready · fps=… worst_ms=… · capture_ms=… frames=… skipped=…
Esc ile çıkılır. Profil Canard'ınkinden ayrıdır (build/engine-bench/qt-profile); içerik ayarları (ör. mscillus modu)
çalıştırmalar arasında korunur.
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QKeySequence, QShortcut
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from canard.web.engine import WebHost

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def emit(line: str) -> None:
    print(line, flush=True)


class _BenchConsole(logging.Handler):
    # WebHost sayfa konsolunu "canard.web" logger'ına yazar; ölçüm satırları buradan stdout'a aktarılır.
    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if "[canard-bench] " in message:
            emit(message.split("[canard-bench] ", 1)[1])


def make_profile() -> QWebEngineProfile:
    # canard.web.engine.create_profile ile aynı ayarlar, ayrı depolama.
    profile = QWebEngineProfile("canard-bench")
    profile.setPersistentStoragePath(str(ROOT / "build" / "engine-bench" / "qt-profile"))
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    script = QWebEngineScript()
    script.setName("canard-bench-fps")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    script.setSourceCode((HERE / "fps_meter.js").read_text(encoding="utf-8"))
    profile.scripts().insert(script)
    return profile


class Capture:
    """Canard.saver hattının aynısı: grabFramebuffer → ARGB32 → (isteğe bağlı) donanım HEVC."""

    def __init__(self, host: WebHost, fps: int, encode: bool):
        self._host = host
        self._fps = fps
        self._encode = encode
        self._encoder = None
        self._quick = host.view.findChild(QQuickWidget)
        self._spent = 0.0
        self._frames = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._capture)
        self._timer.start(max(1, round(1000 / fps)))
        self._report = QTimer()
        self._report.timeout.connect(self._flush)
        self._report.start(1000)

    def _capture(self) -> None:
        started = time.perf_counter()
        image = self._quick.grabFramebuffer() if self._quick is not None else self._host.view.grab().toImage()
        if image.isNull():
            return
        if image.format() != QImage.Format.Format_ARGB32:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)
        if self._encode:
            if self._encoder is None:
                from canard.platform.macos.video import HevcEncoder

                width, height = image.width() & ~1, image.height() & ~1
                self._encoder = HevcEncoder(width, height, self._fps, int(width * height * self._fps * 0.12),
                                            lambda *_: None)
            if image.width() != self._encoder.width or image.height() != self._encoder.height:
                image = image.copy(0, 0, self._encoder.width, self._encoder.height)
            self._encoder.encode(image, time.monotonic_ns() // 1000)
        self._spent += (time.perf_counter() - started) * 1000
        self._frames += 1

    def _flush(self) -> None:
        if self._frames:
            emit(f"capture_ms={self._spent / self._frames:.1f} frames={self._frames} skipped=0")
        self._spent, self._frames = 0.0, 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--boot")
    parser.add_argument("--keys", type=int, default=0)
    parser.add_argument("--capture", type=int, default=0)
    parser.add_argument("--encode", action="store_true")
    parser.add_argument("--system-audio", action="store_true")
    args = parser.parse_args()

    web_log = logging.getLogger("canard.web")
    web_log.setLevel(logging.INFO)
    web_log.addHandler(_BenchConsole())

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv[:1])
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)

    profile = make_profile()
    audio_hub = None
    if args.system_audio:
        from canard.audio.hub import SystemAudioHub
        from canard.platform.base import detect
        from canard.web import system_audio

        audio_hub = SystemAudioHub(detect(), app)
        system_audio.install(profile)
    boot = Path(args.boot).read_text(encoding="utf-8") if args.boot else None

    window = QWidget(None, Qt.WindowType.FramelessWindowHint)
    window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
    window.setWindowTitle("Canard motor kıyası — Qt")
    window.setGeometry(app.primaryScreen().geometry())
    host = WebHost(profile, boot_script=boot, audio_hub=audio_hub)
    layout = QVBoxLayout(window)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(host)
    QShortcut(QKeySequence(Qt.Key.Key_Escape), window, app.quit)
    window.show()
    window.raise_()
    window.activateWindow()

    state = {"capture": None}

    def loaded(ok: bool) -> None:
        if not ok or state.get("ready"):
            return
        state["ready"] = True
        emit("ready")
        for index in range(args.keys):
            QTimer.singleShot(5000 + index * 400, lambda: host.send_key("ArrowRight"))
        if args.capture:
            QTimer.singleShot(1000, lambda: state.update(capture=Capture(host, args.capture, args.encode)))

    host.page.loadFinished.connect(loaded)
    host.load(args.url)
    code = app.exec()
    if audio_hub is not None:
        audio_hub.shutdown()
    import shiboken6

    shiboken6.delete(window)
    shiboken6.delete(profile)
    return code


if __name__ == "__main__":
    sys.exit(main())
