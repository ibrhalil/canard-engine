"""Menü çubuğunu içeriğin rengine boyama (macOS).

macOS menü çubuğunun rengini yalnız sistemin masaüstü resminden alıyor ve her pencere düzeni değişikliğinde yeniden
hesaplıyor; Canard'ın canlı penceresini hiç görmüyor (ölçümler: PLAN.md). Bu yüzden Canard açıkken her ekranda
içeriğin bir karesi masaüstü resmi olarak tutulur; kullanıcının resmi Canard kapanınca ya da duvar kağıdı gizlenince
aynen geri konur. Kare yalnız olaylarda yenilenir: sayfa yüklenince (kaynak, motor, yeni ekran), etkileşimli moddan
çıkınca (içerikte mod genelde o sırada seçilir) ve duvar kağıdı yeniden gösterilince — sürekli izleme yok.

Masaüstü durağan bir resim dosyası değilse (renk, hareketli duvar kağıdı) birebir geri konamayacağı için hiç
dokunulmaz. Kullanıcının resmi kare konmadan önce settings.json'a yazılır: Canard çökerse sonraki açılışta geri konur.
"""

import logging
import time

from PySide6.QtCore import QObject, QTimer

from canard.app.settings import data_location

log = logging.getLogger("canard.menubar")

PENDING_KEY = "menubar.original_desktop"  # ekran no → geri konacak masaüstü resmi
_AFTER_LOAD_MS = 2000  # sayfa ilk karelerini çizsin
_AFTER_EVENT_MS = 1000
_REFRESH_MS = 500  # masaüstü resmi uygulandıktan sonra (WallpaperAgent eşzamansız)


class MenuBarTint(QObject):
    def __init__(self, manager, adapter, store, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._adapter = adapter
        self._store = store
        self._pending: dict[str, dict] = {}  # kare masaüstündeyken kullanıcının resmi
        self._skip_logged = False
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._update)
        manager.pageLoaded.connect(lambda: self._timer.start(_AFTER_LOAD_MS))  # her ekranın yüklemesi süreyi uzatır
        self._restore_saved()

    def interactive_ended(self) -> None:
        self._timer.start(_AFTER_EVENT_MS)

    def wallpaper_shown(self) -> None:
        self._timer.start(_AFTER_EVENT_MS)

    def restore(self) -> None:
        """Kullanıcının resimleri geri (duvar kağıdı gizlendi ya da Canard kapanıyor)."""
        self._timer.stop()
        for key in list(self._pending):
            self._restore(key)

    def close(self) -> None:
        self._closed = True  # kapanışta gelen geç kare (ör. sayfalar kapanırken) masaüstüne yazılmaz
        self.restore()

    def _update(self) -> None:
        for host, screen in self._manager.pages():
            display = self._adapter.screen_display_id(screen)
            if display is None or host.suspended:
                continue
            original = self._pending.get(str(display))  # kare zaten masaüstündeyse kullanıcının resmi saklı
            if original is None:
                try:
                    original = self._adapter.desktop_picture(display)
                except Exception:
                    log.exception("desktop picture could not be read")
                    continue
            if original is None:
                if not self._skip_logged:
                    self._skip_logged = True
                    log.info("desktop is not a still picture (color or dynamic wallpaper): menu bar is not tinted, "
                             "since Canard could not put it back exactly")
                continue
            ratio = screen.devicePixelRatio()
            size = screen.geometry().size()
            host.grab_frame(round(size.width() * ratio), round(size.height() * ratio),
                            lambda image, h=host, d=display, o=original: self._tint(h, d, o, image))

    def _tint(self, host, display: int, original: dict, image) -> None:
        if image is None or self._closed:
            return
        key = str(display)
        try:
            # Her seferinde yeni ad: macOS aynı yolu önbellekten okuyabiliyor.
            path = data_location() / "desktop" / f"{display}-{time.time_ns() // 1_000_000}.jpg"
            frame = self._adapter.write_desktop_frame(image, path)
            self._pending[key] = original
            self._save()  # önce kaydedilir: çökerse sonraki açılışta geri konur
            self._adapter.set_desktop_picture(display, frame)
        except Exception:
            log.exception("menu bar could not be tinted")
            self._restore(key)
            return
        for old in path.parent.glob(f"{display}-*.jpg"):
            if old != path:
                old.unlink(missing_ok=True)
        log.info("display %s: menu bar tinted (content frame is the desktop picture while Canard runs)", display)
        # Menü çubuğu yeni resmi ancak pencere düzeni değişince fark ediyor.
        QTimer.singleShot(_REFRESH_MS, host, lambda: self._manager.refresh_window(host))

    def _restore(self, key: str) -> None:
        original = self._pending.pop(key, None)
        if original is not None:
            try:
                self._adapter.set_desktop_picture(int(key), original)
                log.info("display %s: desktop picture put back", key)
            except Exception:
                log.exception("desktop picture could not be put back: %s", original.get("url"))
                self._pending[key] = original  # settings.json'da kalır, sonraki açılışta yeniden denenir
                self._save()
                return
        self._save()
        for frame in (data_location() / "desktop").glob(f"{key}-*.jpg"):
            frame.unlink(missing_ok=True)

    def _restore_saved(self) -> None:
        saved = self._store.get(PENDING_KEY) or {}
        if saved:
            log.warning("restoring the desktop picture left by an unfinished run: %s", saved)
            self._pending = {str(key): value for key, value in saved.items()}
            self.restore()

    def _save(self) -> None:
        self._store.set(PENDING_KEY, dict(self._pending))
        self._store.save()
