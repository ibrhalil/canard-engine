"""Faz 3a spike B2-2 — Canard'ın web motoru kilitliyken de kare üretebiliyor mu?

QtWebEngine (Canard'ın kullandığı motor) ile animasyonlu bir sayfa açar, her 33 ms'de `grab()` ile kare
alır ve frame_server.py ile aynı protokolle (127.0.0.1:47391, [4 bayt uzunluk][JPEG]) .saver'a gönderir.
Her 2 sn: kare alma süresi, üretilen kare/sn, kaçının bir öncekinden farklı olduğu (animasyon sürüyor mu),
pencerenin macOS'a göre görünürlüğü ve bağlı .saver sayısı loglanır; ekran koruyucu/kilit olayları da.

Çalıştır:  uv run python tools/saver-probe/web_frame_server.py [URL]   (varsayılan: tools/wallpaper-demo)
Pencere açık kalmalı (küçültme); Ctrl+C ile çık.
"""

import queue
import signal
import socket
import struct
import sys
import threading
import time
import zlib
from pathlib import Path

import objc
from AppKit import NSDistributedNotificationCenter, NSWindowOcclusionStateVisible
from Foundation import NSObject
from PySide6.QtCore import QBuffer, QIODevice, Qt, QTimer, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

PORT = 47391
WIDTH, HEIGHT, FPS = 1280, 720, 30
_T0 = time.monotonic()
_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:7.1f}s  {message}", flush=True)


def _serve(conn: socket.socket, address) -> None:
    frames: queue.Queue = queue.Queue(maxsize=2)
    with _clients_lock:
        _clients.append(frames)
    log(f".saver bağlandı: {address[1]}")
    try:
        while True:
            conn.sendall(frames.get())
    except OSError as error:
        log(f".saver bağlantısı bitti: {address[1]} ({error.__class__.__name__})")
    finally:
        with _clients_lock:
            _clients.remove(frames)
        conn.close()


def _accept(server: socket.socket) -> None:
    while True:
        try:
            conn, address = server.accept()
        except OSError:
            return
        threading.Thread(target=_serve, args=(conn, address), daemon=True).start()


def _broadcast(packet: bytes) -> int:
    with _clients_lock:
        targets = list(_clients)
    for frames in targets:
        if frames.full():  # yavaş istemci: en eski kare atılır, gecikme birikmez
            try:
                frames.get_nowait()
            except queue.Empty:
                pass
        frames.put_nowait(packet)
    return len(targets)


class _Events(NSObject):
    def fire_(self, note):
        log(f"[olay] {note.name()}")


def main() -> int:
    app = QApplication(sys.argv[:1])
    target = sys.argv[1] if len(sys.argv) > 1 else None
    url = QUrl(target) if target else QUrl.fromLocalFile(
        str((Path(__file__).parent / ".." / "wallpaper-demo" / "index.html").resolve()))

    view = QWebEngineView()
    view.setWindowTitle("Canard B2-2 kare kaynağı")
    view.resize(WIDTH, HEIGHT)
    view.load(url)
    view.show()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen()
    threading.Thread(target=_accept, args=(server,), daemon=True).start()

    events = _Events.alloc().init()
    center = NSDistributedNotificationCenter.defaultCenter()
    for name in ("com.apple.screensaver.didstart", "com.apple.screensaver.didstop",
                 "com.apple.screenIsLocked", "com.apple.screenIsUnlocked"):
        center.addObserver_selector_name_object_(events, "fire:", name, None)

    stats = {"frames": 0, "changed": 0, "grab_ms": 0.0, "last_hash": None, "since": time.monotonic()}

    def capture():
        started = time.perf_counter()
        image = view.grab().toImage()
        stats["grab_ms"] += (time.perf_counter() - started) * 1000
        stats["frames"] += 1
        digest = zlib.crc32(bytes(image.scaled(32, 18).constBits()))
        if digest != stats["last_hash"]:
            stats["changed"] += 1
            stats["last_hash"] = digest
        if image.width() != WIDTH:
            image = image.scaled(WIDTH, HEIGHT, Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "JPEG", 80)
        jpeg = bytes(buffer.data())
        clients = _broadcast(struct.pack(">I", len(jpeg)) + jpeg)
        now = time.monotonic()
        if now - stats["since"] >= 2:
            frames = max(1, stats["frames"])
            window = objc.objc_object(c_void_p=int(view.winId())).window()
            visible = bool(window.occlusionState() & NSWindowOcclusionStateVisible)
            log(f"üretilen {stats['frames'] / (now - stats['since']):.0f} kare/sn · değişen %{stats['changed'] * 100 // frames}"
                f" · grab {stats['grab_ms'] / frames:.1f} ms · pencere {'GÖRÜNÜR' if visible else 'ÖRTÜLÜ'}"
                f" · .saver {clients}")
            stats.update(frames=0, changed=0, grab_ms=0.0, since=now)

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(capture)
    timer.start(1000 // FPS)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)
    log(f"kaynak: {url.toString()} · dinleniyor 127.0.0.1:{PORT}")
    code = app.exec()
    server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
