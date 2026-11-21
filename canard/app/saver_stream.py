"""Ekran koruyucu kare yayını: Canard.saver'a ekran başına donanım HEVC akışı (Faz 3a, yol B).

macOS kilitli oturumda uygulama pencerelerini gizler; gerçek ekran koruyucu (Canard.saver) içeriği kendisi
çizemez (legacyScreenSaver'da WKWebView animasyonu kısıyor). Bu yüzden Canard duvar kağıdı sayfasını
çizmeye devam eder, kareleri `grab()` ile alır (kilitliyken çalışan tek yol; SCK kilitliyken askıya alınıyor),
donanım HEVC ile kodlar ve yerel TCP ile .saver'a gönderir. Ölçümler: tools/saver-probe, PLAN.md.

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

log = logging.getLogger("canard.saver")

PORT = 47391
DEFAULT_FPS = 30  # 30, 60/120 Hz ekranlara eşit bölünür; yetişilemezse zamanlayıcı kendiliğinden yavaşlar
_BITS_PER_PIXEL_FRAME = 0.12  # HEVC için cömert bit bütçesi (ör. 3420×2224 @24 ≈ 22 Mbit/sn)
_MAX_PENDING_BYTES = 8 * 1024 * 1024  # .saver yetişemiyorsa kare atlanır, gecikme birikmez
_MAX_HANDSHAKE = 4096


def _packet(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack(">I", len(payload)) + payload


class SaverSession(QObject):
    _encoded = Signal(bytes)  # VideoToolbox iş parçacığından ana iş parçacığına

    def __init__(self, socket, manager, adapter, fps: int, parent=None):
        super().__init__(parent)
        self._socket = socket
        self._manager = manager
        self._adapter = adapter
        self._fps = fps
        self._buffer = b""
        self._host = None
        self._encoder = None
        self._last_hvcc = None
        self._frames = 0
        self._dropped = 0
        self._capture_ms = 0.0
        self._quick = None
        self._name = "?"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._capture)
        self._stats = QTimer(self)
        self._stats.timeout.connect(self._report)
        self._encoded.connect(self._send)
        socket.readyRead.connect(self._read_handshake)
        socket.disconnected.connect(self.close)

    def _read_handshake(self) -> None:
        self._buffer += bytes(self._socket.readAll())
        if b"\n" not in self._buffer:
            if len(self._buffer) > _MAX_HANDSHAKE:
                self._fail("el sıkışma çok uzun")
            return
        self._socket.readyRead.disconnect(self._read_handshake)
        line = self._buffer.split(b"\n", 1)[0]
        try:
            request = json.loads(line)
            display = int(request["display"])
            width, height = int(request["width"]), int(request["height"])
        except (ValueError, KeyError, TypeError):
            self._fail("geçersiz el sıkışma")
            return
        if not (16 <= width <= 8192 and 16 <= height <= 8192):
            self._fail("geçersiz boyut")
            return
        acquired = self._manager.acquire_stream(display)
        if acquired is None:
            self._fail(f"bu ekran için içerik yok (display {display})")
            return
        self._host, self._name = acquired
        # QWebEngineView'in içindeki QQuickWidget: grabFramebuffer() doğrudan GPU tamponunu verir
        # (Retina tam ekranda 9.3 ms; QWidget.grab()+toImage 16.7 ms).
        self._quick = self._host.view.findChild(QQuickWidget)
        width, height = width & ~1, height & ~1  # HEVC çift boyut ister
        bitrate = int(width * height * self._fps * _BITS_PER_PIXEL_FRAME)
        try:
            self._encoder = self._adapter.create_video_encoder(width, height, self._fps, bitrate, self._on_output)
        except (OSError, NotImplementedError) as error:
            self._fail(f"kodlayıcı açılamadı: {error}")
            return
        hello = {"v": 1, "codec": "hvc1", "width": width, "height": height, "fps": self._fps}
        self._socket.write(_packet(b"C", json.dumps(hello).encode()))
        self._timer.start(max(1, round(1000 / self._fps)))
        self._stats.start(10_000)
        log.info("ekran koruyucu bağlandı: %s · %dx%d @ %d kare/sn · %.0f Mbit/sn", self._name, width, height,
                 self._fps, bitrate / 1e6)

    def _capture(self) -> None:
        if self._socket.bytesToWrite() > _MAX_PENDING_BYTES:
            self._dropped += 1
            return
        started = time.perf_counter()
        pts_us = time.monotonic_ns() // 1000
        image = self._quick.grabFramebuffer() if self._quick is not None else self._host.view.grab().toImage()
        if image.isNull():
            return
        if image.format() != QImage.Format.Format_ARGB32:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)  # bellekte BGRA (CoreVideo RGBA desteklemiyor)
        if image.width() != self._encoder.width or image.height() != self._encoder.height:
            image = image.scaled(self._encoder.width, self._encoder.height)  # yalnız istisna (ör. etkileşimli boşluk)
        self._encoder.encode(image, pts_us)
        self._frames += 1
        self._capture_ms += (time.perf_counter() - started) * 1000

    def _on_output(self, keyframe: bool, pts_us: int, hvcc: bytes | None, data: bytes) -> None:
        # VideoToolbox iş parçacığı: yalnız paket hazırlanır, soket ana iş parçacığında yazılır.
        out = b""
        if hvcc is not None and hvcc != self._last_hvcc:
            self._last_hvcc = hvcc
            out += _packet(b"H", hvcc)
        out += _packet(b"F", bytes([1 if keyframe else 0]) + struct.pack(">Q", pts_us) + data)
        self._encoded.emit(out)

    def _send(self, data: bytes) -> None:
        if self._socket is not None:
            self._socket.write(data)

    def _report(self) -> None:
        log.debug("%s: %.1f kare/sn gönderildi, yakalama+kodlama %.1f ms/kare, %d atlandı (bekleyen %d KB)",
                  self._name, self._frames / 10, self._capture_ms / max(1, self._frames), self._dropped,
                  self._socket.bytesToWrite() // 1024)
        self._frames = self._dropped = 0
        self._capture_ms = 0.0

    def _fail(self, message: str) -> None:
        log.warning("ekran koruyucu bağlantısı reddedildi: %s", message)
        self._socket.write(_packet(b"E", message.encode()))
        self._socket.disconnectFromHost()

    def close(self) -> None:
        if self._socket is None:
            return
        self._timer.stop()
        self._stats.stop()
        if self._encoder is not None:
            self._encoder.close()
            self._encoder = None
        if self._host is not None:
            self._manager.release_stream(self._host)
            log.info("ekran koruyucu ayrıldı: %s", self._name)
            self._host = None
        socket, self._socket = self._socket, None
        socket.deleteLater()
        self.deleteLater()


class SaverStreamServer(QObject):
    def __init__(self, manager, adapter, fps: int = DEFAULT_FPS, port: int = PORT, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._adapter = adapter
        self._fps = fps
        self._sessions: list[SaverSession] = []
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._accept)
        # Yalnız bu bilgisayardan erişilebilir; .saver kum havuzunda anahtar okuyamadığı için kimlik doğrulama yok.
        if self._server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), port):
            log.info("ekran koruyucu yayını hazır: 127.0.0.1:%d (Canard.saver bağlanınca başlar)", port)
        else:
            log.warning("ekran koruyucu yayını açılamadı (port %d: %s); Canard.saver içerik gösteremez",
                        port, self._server.errorString())

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            session = SaverSession(socket, self._manager, self._adapter, self._fps, self)
            self._sessions.append(session)
            session.destroyed.connect(lambda *_, s=session: self._sessions.remove(s) if s in self._sessions else None)

    def close(self) -> None:
        self._server.close()
        for session in list(self._sessions):
            session.close()
        self._sessions.clear()
