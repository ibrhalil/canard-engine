"""Faz 3a spike B2-1 — test kare sunucusu.

.saver'a 127.0.0.1:47391 üzerinden ham TCP ile kare gönderir: her kare [4 bayt uzunluk (big-endian)][JPEG].
Kare: 1280×720 test deseni (kayan çubuk + kare numarası + saat), saniyede 30.

Çalıştır:  uv run python tools/saver-probe/frame_server.py      (Ctrl+C ile çık)
"""

import signal
import socket
import struct
import sys
import threading
import time

from PySide6.QtCore import QBuffer, QIODevice, QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

PORT = 47391
WIDTH, HEIGHT, FPS = 1280, 720, 30
_T0 = time.monotonic()


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:7.1f}s  {message}", flush=True)


def render(number: int) -> bytes:
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor(6, 7, 13))
    painter = QPainter(image)
    x = int((number * 12) % WIDTH)
    painter.fillRect(x, 0, 60, HEIGHT, QColor(255, 80, 160))
    painter.setPen(QColor(232, 232, 255))
    painter.setFont(QFont("Helvetica", 40))
    painter.drawText(QRect(0, 0, WIDTH, HEIGHT), Qt.AlignmentFlag.AlignCenter,
                     f"Canard B2 kare aktarımı\nkare {number}\n{time.strftime('%H:%M:%S')}")
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 80)
    return bytes(buffer.data())


def serve(conn: socket.socket, address) -> None:
    name = f"{address[0]}:{address[1]}"
    log(f"bağlandı: {name}")
    sent = total = 0
    window_start = time.monotonic()
    next_frame = time.monotonic()
    try:
        while True:
            jpeg = render(total)
            conn.sendall(struct.pack(">I", len(jpeg)) + jpeg)
            sent += 1
            total += 1
            now = time.monotonic()
            if now - window_start >= 5:
                log(f"{name}: {sent / (now - window_start):.1f} kare/sn gönderildi ({len(jpeg) // 1024} KB/kare)")
                sent, window_start = 0, now
            next_frame += 1 / FPS
            time.sleep(max(0.0, next_frame - time.monotonic()))
    except OSError as error:
        log(f"bağlantı bitti: {name} ({error.__class__.__name__}), toplam {total} kare")
    finally:
        conn.close()


def main() -> int:
    QGuiApplication([sys.argv[0], "-platform", "offscreen"])  # metin çizimi için yazı tipleri
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen()
    signal.signal(signal.SIGINT, lambda *_: (log("çıkılıyor"), server.close(), sys.exit(0)))
    log(f"dinleniyor: 127.0.0.1:{PORT} · {WIDTH}x{HEIGHT} @ {FPS} kare/sn")
    while True:
        try:
            conn, address = server.accept()
        except OSError:
            return 0
        threading.Thread(target=serve, args=(conn, address), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
