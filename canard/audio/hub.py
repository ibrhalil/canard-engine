"""Sistem sesini sayfalara dağıtan merkez.

Yakalama yalnızca en az bir sayfa ses istiyor ve duraklatılmamışken çalışır. Ses QWebChannel
üzerinden base64 float32 PCM olarak iletilir: açık port ve token yok, başka süreç dinleyemez.
"""

import base64
import logging

from PySide6.QtCore import QObject, Signal, Slot

# ~20 ms'lik paketler: tap 512 karelik (~10.7 ms) tamponları ikişer ikişer gönderilir.
_BATCH_SECONDS = 0.02

log = logging.getLogger("canard.audio")


class PageAudio(QObject):
    """Tek bir sayfaya `canardAudio` adıyla açılan nesne (bkz. canard/web/system_audio.js)."""

    chunk = Signal(str)
    formatChanged = Signal("QVariantMap")

    def __init__(self, hub: "SystemAudioHub", parent=None):
        super().__init__(parent)
        self._hub = hub
        self.streams = 0
        self.paused = False

    @Slot(result="QVariantMap")
    def start(self) -> dict:
        self.streams += 1
        result = self._hub.update()
        if not result.get("ok"):
            self.streams -= 1
        return result

    @Slot()
    def stop(self) -> None:
        if self.streams > 0:
            self.streams -= 1
            self._hub.update()

    @property
    def active(self) -> bool:
        return self.streams > 0 and not self.paused

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._hub.update()

    def reset(self) -> None:
        if self.streams:
            self.streams = 0
            self._hub.update()


class SystemAudioHub(QObject):
    _captured = Signal(bytes)
    _deviceChanged = Signal()

    def __init__(self, adapter, parent=None):
        super().__init__(parent)
        self._adapter = adapter
        self._pages: list[PageAudio] = []
        self._capture = None
        self._pending = bytearray()
        self._batch_bytes = 0
        self._format: dict | None = None
        self._error: str | None = None
        self._captured.connect(self._dispatch)
        self._deviceChanged.connect(self._on_device_changed)

    def page(self) -> PageAudio:
        page_audio = PageAudio(self, self)
        self._pages.append(page_audio)
        return page_audio

    def forget(self, page_audio: PageAudio) -> None:
        if page_audio in self._pages:
            self._pages.remove(page_audio)
            self.update()

    def update(self) -> dict:
        wanted = any(p.active for p in self._pages)
        log.debug("abone sayfa: %d/%d etkin", sum(p.active for p in self._pages), len(self._pages))
        if wanted and self._format is None:
            self._start()
        elif not wanted and self._format is not None:
            self._stop()
        if self._format is not None:
            return {"ok": True, **self._format}
        return {"ok": False, "error": self._error or "Sistem sesi başlatılamadı"}

    def shutdown(self) -> None:
        self._pages.clear()
        self._stop()

    def _start(self) -> None:
        try:
            if self._capture is None:
                self._capture = self._adapter.create_system_audio(self._captured.emit, self._deviceChanged.emit)
            fmt = self._capture.start()
        except NotImplementedError:
            self._error = "Bu platformda sistem sesi yakalama henüz yok"
            return
        except Exception as error:
            self._error = f"Sistem sesi başlatılamadı: {error}"
            log.error("%s", self._error, exc_info=True)
            return
        self._set_format(fmt)
        log.info("sistem sesi yakalanıyor (%.0f Hz, %d kanal)", fmt.sample_rate, fmt.channels)

    def _stop(self) -> None:
        if self._capture is not None and self._format is not None:
            self._capture.stop()
            log.info("sistem sesi durdu")
        self._format = None
        self._pending.clear()

    def _set_format(self, fmt) -> None:
        self._format = {"sampleRate": fmt.sample_rate, "channels": fmt.channels, "interleaved": fmt.interleaved}
        self._batch_bytes = int(fmt.sample_rate * _BATCH_SECONDS) * fmt.channels * 4 if fmt.interleaved else 0
        self._pending.clear()

    def _dispatch(self, data: bytes) -> None:
        if self._format is None:
            return
        if self._batch_bytes:
            self._pending += data
            if len(self._pending) < self._batch_bytes:
                return
            data = bytes(self._pending)
            self._pending.clear()
        encoded = base64.b64encode(data).decode("ascii")
        for page_audio in self._pages:
            if page_audio.active:
                page_audio.chunk.emit(encoded)

    def _on_device_changed(self) -> None:
        if self._capture is None or self._format is None:
            return
        try:
            fmt = self._capture.restart()
        except Exception as error:
            self._error = f"Çıkış aygıtı değişti, sistem sesi yeniden kurulamadı: {error}"
            log.error("%s", self._error, exc_info=True)
            self._format = None
            return
        previous = self._format
        self._set_format(fmt)
        log.info("çıkış aygıtı değişti, yakalama yeniden kuruldu")
        if previous != self._format:
            for page_audio in self._pages:
                page_audio.formatChanged.emit(self._format)
