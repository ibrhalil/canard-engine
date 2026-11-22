"""canard/resources/canard-macos.svg'den macOS paket görsellerini üretir (Xcode gerekmez; Qt SVG + iconutil).

  render_icon.py icns <çıktı.icns>        → uygulama ikonu (16–1024 px, @1x/@2x)
  render_icon.py thumbnail <Resources/>   → ekran koruyucu küçük resmi: thumbnail.png (90×58) + thumbnail@2x.png (180×116)

Ekranda pencere açmaz (QT_QPA_PLATFORM=offscreen).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.fonts=false")  # metin çizilmiyor; font uyarısı gürültü

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

SOURCE = Path(__file__).resolve().parents[1] / "canard" / "resources" / "canard-macos.svg"
_ICONSET = (16, 32, 128, 256, 512)
_THUMBNAIL = (90, 58)
_BACKGROUND = ("#16263f", "#070d19")  # SVG'deki #bg geçişi


def _image(width: int, height: int) -> tuple[QImage, QPainter]:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return image, painter


def icns(out: Path, renderer: QSvgRenderer) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Canard.iconset"
        iconset.mkdir()
        for size in _ICONSET:
            for scale, suffix in ((1, ""), (2, "@2x")):
                image, painter = _image(size * scale, size * scale)
                renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
                painter.end()
                image.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
        subprocess.run(["iconutil", "-c", "icns", "-o", str(out), str(iconset)], check=True)


def thumbnail(resources: Path, renderer: QSvgRenderer) -> None:
    # Ekran koruyucu listesi 90×58 küçük resim bekler (sistemin Random.saver'ı gibi): zemin tam alan, işaret ortada.
    mark = renderer.boundsOnElement("mark")  # çizgi kalınlığı hariç; çizgiler bu kutudan biraz taşar
    for scale, suffix in ((1, ""), (2, "@2x")):
        width, height = _THUMBNAIL[0] * scale, _THUMBNAIL[1] * scale
        image, painter = _image(width, height)
        gradient = QLinearGradient(0, 0, 0, height)
        gradient.setColorAt(0, QColor(_BACKGROUND[0]))
        gradient.setColorAt(1, QColor(_BACKGROUND[1]))
        painter.fillRect(0, 0, width, height, gradient)
        mark_height = height * 0.62
        mark_width = mark_height * mark.width() / mark.height()
        renderer.render(painter, "mark", QRectF((width - mark_width) / 2, (height - mark_height) / 2,
                                                mark_width, mark_height))
        painter.end()
        image.save(str(resources / f"thumbnail{suffix}.png"))


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in ("icns", "thumbnail"):
        print(__doc__, file=sys.stderr)
        return 2
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 — QPainter/QSvgRenderer için
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"SVG okunamadı: {SOURCE}", file=sys.stderr)
        return 1
    target = Path(sys.argv[2])
    (icns if sys.argv[1] == "icns" else thumbnail)(target, renderer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
