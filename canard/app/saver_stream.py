"""Ekran koruyucu kare yayını: Canard.saver'a ekran başına donanım HEVC akışı.

Canard.saver içeriği kendisi çizemez (kilitli oturum, legacyScreenSaver kısıtları); Canard sayfayı çizer, kareyi
yakalar, HEVC'ye kodlar ve yerel TCP ile gönderir. Ekran başına tek yakalama + tek kodlayıcı; aynı ekrana açılan
her bağlantı aynı kareleri alır. Gerekçe ve ölçümler: PLAN.md, tools/saver-probe.

Protokol (127.0.0.1:47391, tek yönlü kare akışı):
  .saver → Canard   tek satır JSON + "\\n": {"v": 1, "display": <CGDirectDisplayID>, "width": px, "height": px}
  Canard → .saver   paketler: [1 bayt tür][4 bayt uzunluk, big-endian][yük]
      b"C"  JSON {"v": 1, "codec": "hvc1", "width": px, "height": px, "fps": n}  — oturum başında
      b"H"  hvcC kutusu (kod çözücü parametre setleri) — ilk anahtar karede ve değişince
      b"F"  [1 bayt bayrak: bit0 = anahtar kare][8 bayt yakalanma anı µs, big-endian][4 bayt uzunluk önekli
            HEVC NAL birimleri] — .saver kareleri bu anlara göre sabit gecikmeyle, eşit aralıkla gösterir
      b"E"  UTF-8 hata metni; ardından bağlantı kapanır
"""

import json
import logging
import struct
import time

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtQuickWidgets import QQuickWidget

from canard.i18n import t

log = logging.getLogger("canard.saver")

PORT = 47391
DEFAULT_FPS = 30  # 30, 60/120 Hz ekranlara eşit bölünür; yetişilemezse zamanlayıcı kendiliğinden yavaşlar
_BITS_PER_PIXEL_FRAME = 0.12  # HEVC için cömert bit bütçesi (ör. 3420×2224 @24 ≈ 22 Mbit/sn)
_MAX_PENDING_BYTES = 8 * 1024 * 1024  # .saver yetişemiyorsa kare atlanır, gecikme birikmez
_MAX_HANDSHAKE = 4096


def _packet(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack(">I", len(payload)) + payload


class DisplayStream(QObject):
    """Bir ekranın sayfasını yakalayıp bir kez kodlar ve o ekrana bağlı tüm Canard.saver oturumlarına dağıtır."""

    _encoded = Signal(bool, object, object, bytes)  # VT iş parçacığından ana iş parçacığına; µs zaman 32 bite sığmaz

    def __init__(self, host, name: str, width: int, height: int, fps: int, adapter, on_empty, parent=None):
        super().__init__(parent)
        self.host = host
        self.name = name
        self.width, self.height, self.fps = width, height, fps
        self._on_empty = on_empty
        self._sessions: list[SaverSession] = []
        self._force_keyframe = False
        self._frames = 0
        self._dropped = 0
        self._capture_ms = 0.0
        # grabFramebuffer() GPU tamponunu doğrudan verir; QWidget.grab()'dan ~2 kat hızlı.
        self._quick = host.view.findChild(QQuickWidget)
        self.bitrate = int(width * height * fps * _BITS_PER_PIXEL_FRAME)
        self._encoder = adapter.create_video_encoder(width, height, fps, self.bitrate, self._on_output)
        self._encoded.connect(self._deliver)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._capture)
        self._timer.start(max(1, round(1000 / fps)))
        self._stats = QTimer(self)
        self._stats.timeout.connect(self._report)
        self._stats.start(10_000)

    def add(self, session: "SaverSession") -> None:
        self._sessions.append(session)
        self._force_keyframe = True  # yeni katılan 2 sn'lik anahtar kare aralığını beklemesin

    def remove(self, session: "SaverSession") -> None:
        if session in self._sessions:
            self._sessions.remove(session)
        if not self._sessions:
            self.close()
            self._on_empty(self)

    @property
    def sessions(self) -> int:
        return len(self._sessions)

    def _capture(self) -> None:
        # Kare tek kodlandığı için en yavaş bağlantı belirler (atlanan P-karesi kod çözmeyi bozar).
        if any(session.backlogged for session in self._sessions):
            self._dropped += 1
            return
        started = time.perf_counter()
        pts_us = time.monotonic_ns() // 1000
        image = self._quick.grabFramebuffer() if self._quick is not None else self.host.view.grab().toImage()
        if image.isNull():
            return
        if image.format() != QImage.Format.Format_ARGB32:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)  # bellekte BGRA (CoreVideo RGBA desteklemiyor)
        if image.width() != self.width or image.height() != self.height:
            image = image.scaled(self.width, self.height)  # yalnız istisna (ör. etkileşimli boşluk)
        force, self._force_keyframe = self._force_keyframe, False
        self._encoder.encode(image, pts_us, force_keyframe=force)
        self._frames += 1
        self._capture_ms += (time.perf_counter() - started) * 1000

    def _on_output(self, keyframe: bool, pts_us: int, hvcc: bytes | None, data: bytes) -> None:
        self._encoded.emit(keyframe, pts_us, hvcc, data)

    def _deliver(self, keyframe: bool, pts_us: int, hvcc, data: bytes) -> None:
        frame = _packet(b"F", bytes([1 if keyframe else 0]) + struct.pack(">Q", pts_us) + data)
        for session in list(self._sessions):
            session.deliver(keyframe, hvcc, frame)

    def _report(self) -> None:
        log.debug("%s: %.1f fps sent, capture+encode %.1f ms/frame, %d skipped, %d sessions",
                  self.name, self._frames / 10, self._capture_ms / max(1, self._frames), self._dropped,
                  len(self._sessions))
        self._frames = self._dropped = 0
        self._capture_ms = 0.0

    def close(self) -> None:
        self._timer.stop()
        self._stats.stop()
        if self._encoder is not None:
            self._encoder.close()
            self._encoder = None


class SaverSession(QObject):
    """Tek bir Canard.saver bağlantısı: el sıkışma, ekranın ortak yayınına katılma, kareleri sokete yazma."""

    def __init__(self, socket, server: "SaverStreamServer", parent=None):
        super().__init__(parent)
        self._socket = socket
        self._server = server
        self._buffer = b""
        self._stream: DisplayStream | None = None
        self._waiting_keyframe = True
        self._last_hvcc = None
        socket.readyRead.connect(self._read_handshake)
        socket.disconnected.connect(self.close)

    @property
    def backlogged(self) -> bool:
        return self._socket is not None and self._socket.bytesToWrite() > _MAX_PENDING_BYTES

    def _read_handshake(self) -> None:
        self._buffer += bytes(self._socket.readAll())
        if b"\n" not in self._buffer:
            if len(self._buffer) > _MAX_HANDSHAKE:
                self._fail(t("Handshake too long"))
            return
        self._socket.readyRead.disconnect(self._read_handshake)
        line = self._buffer.split(b"\n", 1)[0]
        try:
            request = json.loads(line)
            display = int(request["display"])
            width, height = int(request["width"]), int(request["height"])
        except (ValueError, KeyError, TypeError):
            self._fail(t("Invalid handshake"))
            return
        if not (16 <= width <= 8192 and 16 <= height <= 8192):
            self._fail(t("Invalid size"))
            return
        try:
            stream, joined = self._server.stream_for(display, width & ~1, height & ~1)  # HEVC çift boyut ister
        except LookupError:
            self._fail(t("No Canard content on this screen\nTurn the screen on in the Canard menu › Screens"))
            return
        except (OSError, NotImplementedError) as error:
            self._fail(t("Could not open the encoder: {error}", error=error))
            return
        self._stream = stream
        hello = {"v": 1, "codec": "hvc1", "width": stream.width, "height": stream.height, "fps": stream.fps}
        self._socket.write(_packet(b"C", json.dumps(hello).encode()))
        stream.add(self)
        if joined:
            log.info("screensaver connected: %s · joined running stream (%d sessions, one encoder)", stream.name,
                     stream.sessions)
        else:
            log.info("screensaver connected: %s · %dx%d @ %d fps · %.0f Mbit/s", stream.name, stream.width,
                     stream.height, stream.fps, stream.bitrate / 1e6)

    def deliver(self, keyframe: bool, hvcc, frame: bytes) -> None:
        if self._socket is None:
            return
        if self._waiting_keyframe:
            if not keyframe:
                return  # katıldıktan sonra ilk anahtar kareye kadar P-kareleri çözülemez
            self._waiting_keyframe = False
        if hvcc is not None and hvcc != self._last_hvcc:
            self._last_hvcc = hvcc
            self._socket.write(_packet(b"H", hvcc))
        self._socket.write(frame)

    def _fail(self, message: str) -> None:
        log.warning("screensaver connection refused: %s", message)
        self._socket.write(_packet(b"E", message.encode()))
        self._socket.disconnectFromHost()

    def close(self) -> None:
        if self._socket is None:
            return
        stream, self._stream = self._stream, None
        if stream is not None:
            log.info("screensaver disconnected: %s", stream.name)
            stream.remove(self)
        socket, self._socket = self._socket, None
        socket.deleteLater()
        self.deleteLater()


class SaverStreamServer(QObject):
    """Yayın kapalıyken de dinler ve bağlananı nedeniyle reddeder: Canard.saver "Canard çalışmıyor" yerine bunu gösterir."""

    def __init__(self, manager, adapter, fps: int = DEFAULT_FPS, port: int = PORT, enabled: bool = True,
                 off_hint=lambda: "", parent=None):
        super().__init__(parent)
        self._manager = manager
        self._adapter = adapter
        self._fps = fps
        self._enabled = enabled
        self._off_hint = off_hint
        self._sessions: list[SaverSession] = []
        self._streams: dict[tuple[int, int, int], DisplayStream] = {}
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        # Yalnız yerel erişim; .saver kum havuzunda anahtar okuyamadığı için kimlik doğrulama yok.
        if self._server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), port):
            if enabled:
                log.info("screensaver stream ready: 127.0.0.1:%d (starts when Canard.saver connects)", port)
            else:
                log.info("screensaver stream off (Canard.saver is told why)")
        else:
            log.warning("could not open the screensaver stream (port %d: %s); Canard.saver cannot show content",
                        port, self._server.errorString())

    def set_enabled(self, enabled: bool) -> None:
        if enabled == self._enabled:
            return
        self._enabled = enabled
        if not enabled:
            for session in list(self._sessions):
                session.close()  # Canard.saver yeniden bağlanınca nedeni öğrenir
        log.info("screensaver stream %s", "on" if enabled else "off")

    def set_fps(self, fps: int) -> None:
        """Yeni yayınlarda geçerli (süren yayın kendi hızıyla devam eder)."""
        self._fps = fps

    def stream_for(self, display: int, width: int, height: int) -> tuple[DisplayStream, bool]:
        """(yayın, süren yayına mı katıldı). İçerik yoksa LookupError, kodlayıcı açılamazsa OSError."""
        key = (display, width, height)
        stream = self._streams.get(key)
        if stream is not None:
            return stream, True
        acquired = self._manager.acquire_stream(display)
        if acquired is None:
            raise LookupError(display)
        host, name = acquired
        try:
            stream = DisplayStream(host, name, width, height, self._fps, self._adapter,
                                   lambda s, k=key: self._stream_ended(k, s), self)
        except BaseException:
            self._manager.release_stream(host)
            raise
        self._streams[key] = stream
        return stream, False

    def _stream_ended(self, key, stream: DisplayStream) -> None:
        if self._streams.get(key) is stream:
            del self._streams[key]
        self._manager.release_stream(stream.host)
        stream.deleteLater()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if not self._enabled:
                self._refuse(socket)
                continue
            session = SaverSession(socket, self, self)
            self._sessions.append(session)
            session.destroyed.connect(lambda *_, s=session: self._sessions.remove(s) if s in self._sessions else None)

    def _refuse(self, socket) -> None:
        # El sıkışma okunmadan kapatılırsa TCP sıfırlanır ve mesaj ulaşmaz.
        message = _packet(b"E", f"{t('Screensaver stream is off')}\n{self._off_hint()}".strip().encode())

        def reply():
            socket.readAll()
            socket.write(message)
            socket.disconnectFromHost()

        socket.readyRead.connect(reply)
        socket.disconnected.connect(socket.deleteLater)

    def close(self) -> None:
        self._server.close()
        for session in list(self._sessions):
            session.close()
        self._sessions.clear()
        for stream in list(self._streams.values()):
            stream.close()
        self._streams.clear()
