"""Motor arayüzü: Canard'ın geri kalanı (WallpaperManager, Runtime, Canard.saver yayını) yalnız bunu görür.

WebEngine bir motorun ortak durumudur (kalıcı depolama, sayfalara enjekte edilen betikler); WebHost tek bir sayfadır
(içerik görünümü + yükleme hatası ekranı). Arka uçlar: canard/web/chromium (QtWebEngine), canard/web/webkit
(WKWebView, yalnız macOS).
"""

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from canard.i18n import t

log = logging.getLogger("canard.web")

media_listeners: list = []  # sayfa mikrofon isteyince çağrılır (Runtime: OS izni kapalıysa kullanıcıya söyler)

_SEND_KEY = ("(function(k){var o={key:k,code:k,bubbles:true,cancelable:true};"
             "document.dispatchEvent(new KeyboardEvent('keydown',o));"
             "document.dispatchEvent(new KeyboardEvent('keyup',o));})(%s);")


def notify_media_request(kind: str) -> None:
    for listener in media_listeners:
        listener(kind)


def unload_pages(hosts, timeout_ms: int = 1000) -> None:
    """Sayfalar silinmeden önce içeriğin pagehide/unload işleyicileri çalışsın (ör. ayarlarını kaydetsin). Olay
    döngüsü en çok `timeout_ms` döndürülür; kapanışta da çağrılabilir."""
    pending: set[int] = set()
    loop = QEventLoop()

    def finished(key: int) -> None:
        pending.discard(key)
        if not pending:
            loop.quit()

    for host in hosts:
        key = id(host)
        pending.add(key)
        if not host.unload(lambda k=key: finished(k)):
            pending.discard(key)
    if pending:
        QTimer.singleShot(timeout_ms, loop, loop.quit)
        loop.exec()


class WebEngine:
    name = ""
    label = ""

    def version(self) -> str:
        """Hakkında penceresi için, ör. "Chromium 140.0…"."""
        raise NotImplementedError

    def data_location(self) -> Path:
        """İçeriklerin localStorage, IndexedDB, çerez ve önbelleği."""
        raise NotImplementedError

    def set_scripts(self, bridge: str, system_audio: bool) -> None:
        """Her sayfaya belge oluşturulurken enjekte edilenler: window.canard köprüsü ve sistem sesi köprüsü.
        Sonraki yüklemede geçerli."""
        raise NotImplementedError

    def create_host(self, boot_script: str | None = None, audio_hub=None) -> "WebHost":
        raise NotImplementedError

    def close(self) -> None:
        """Sayfalar silindikten sonra çağrılır; tekrar çağrılabilir."""


class WebHost(QWidget):
    """Tek sayfa. Alt sınıf görünümü `_set_view` ile verir ve `_load`, `run_js`, `_apply_suspended`, `grab_frame`'i
    sağlar; yükleme bitince `_loaded(ok)`, içerik süreci ölünce `_show_error()` çağırır."""

    def __init__(self, boot_script: str | None = None, parent=None):
        super().__init__(parent)
        self._current_url: str | None = None
        self._boot_script = boot_script
        self._suspended = False
        self._load_started = time.monotonic()
        self._unloading = None  # unload() geri çağrısı: boş sayfaya geçiş bitince
        self.page_audio = None

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._retry_button = retry_button = QPushButton()
        retry_button.clicked.connect(self.retry)
        overlay = QWidget()
        layout = QVBoxLayout(overlay)
        layout.addStretch(1)
        layout.addWidget(self._error_label)
        layout.addWidget(retry_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        self._stack = QStackedLayout(self)
        self._stack.addWidget(overlay)

    def _set_view(self, view: QWidget) -> None:
        self._stack.insertWidget(0, view)
        self._stack.setCurrentIndex(0)

    def load(self, url: str) -> None:
        self._current_url = url
        self._load_started = time.monotonic()
        log.debug("loading: %s", url)
        self._stack.setCurrentIndex(0)
        self._load(url)

    def set_boot_script(self, boot_script: str | None) -> None:
        """Sonraki yüklemede geçerli."""
        self._boot_script = boot_script

    def retry(self) -> None:
        if self._current_url:
            self.load(self._current_url)

    def send_key(self, key: str) -> None:
        self.run_js(_SEND_KEY % json.dumps(key))

    @property
    def suspended(self) -> bool:
        return self._suspended

    def set_suspended(self, suspended: bool) -> None:
        """Duraklatılan sayfa çizmez; sistem sesi bu sayfaya gitmez."""
        if suspended == self._suspended:
            return
        self._suspended = suspended
        if self.page_audio is not None:
            self.page_audio.set_paused(suspended)
        self._apply_suspended(suspended)

    def unload(self, done: Callable[[], None]) -> bool:
        """Boş sayfaya geçer: içeriğin pagehide/unload işleyicileri çalışır (sayfayı silmek bunu yapmıyor; iki
        motorda da ölçüldü). Geçiş bitince `done()`; yüklü sayfa yoksa False döner (bkz. unload_pages)."""
        if self._current_url is None:
            return False
        self._unloading = done
        self._load_blank()
        return True

    def _load_blank(self) -> None:
        raise NotImplementedError

    def grab_frame(self, width: int, height: int, callback: Callable) -> None:
        """Sayfanın güncel karesi `callback(kare)` ile verilir (eşzamanlı ya da sonra, ana iş parçacığında):
        Format_ARGB32 QImage ya da CGImage (kodlayıcı ikisini de alır), alınamazsa None."""
        raise NotImplementedError

    def run_js(self, source: str) -> None:
        raise NotImplementedError

    def _load(self, url: str) -> None:
        raise NotImplementedError

    def _apply_suspended(self, suspended: bool) -> None:
        raise NotImplementedError

    def _loaded(self, ok: bool, url: str = "") -> None:
        if self._unloading is not None:
            done, self._unloading = self._unloading, None
            done()
            return
        elapsed = time.monotonic() - self._load_started
        if ok:
            log.debug("loaded (%.1f s): %s", elapsed, url or self._current_url)
            self._stack.setCurrentIndex(0)
            if self._boot_script:
                log.debug("running boot script")
                self.run_js(self._boot_script)
        else:
            log.error("content failed to load (%.1f s): %s", elapsed, self._current_url)
            self._show_error()

    def _show_error(self) -> None:
        self._error_label.setText(t("Content could not be loaded:\n{url}", url=self._current_url or ""))
        self._retry_button.setText(t("Retry"))
        self._stack.setCurrentIndex(1)
