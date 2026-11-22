"""Ekran başına duvar kağıdı penceresi: oluşturma, park etme, görünürlüğe göre dondurma, Canard.saver yayını."""

import logging
import time
from typing import NamedTuple

import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QVBoxLayout, QWidget

from canard.platform.base import detect
from canard.web.engine import WebHost

log = logging.getLogger("canard.wallpaper")
screen_log = logging.getLogger("canard.screen")
hotkey_log = logging.getLogger("canard.hotkey")

# Ekran olayları AppKit pencereleri taşırken işlenirse libqcocoa çöküyor: biriktirilip sonra uzlaştırılır.
_SCREEN_SETTLE_MS = 500
# Çıkarılan ekranın sayfası bu süre park edilir; ekran dönerse sayfa (sessionStorage dahil) kaldığı yerden sürer.
_PARK_SECONDS = 600
_REFRESH_DELAY_MS = 250
_REFRESH_SETTLE_MS = 500
# Ekran koruyucu başlarken macOS önce "örtülü" bildirir, Canard.saver ~1 sn sonra bağlanır: hemen dondurmak sesi keser.
OCCLUSION_GRACE_MS = 3000


class _Entry(NamedTuple):
    window: QWidget
    host: WebHost
    token: object
    screen: object


def _screen_key(screen) -> int | None:
    if screen is None or not shiboken6.isValid(screen):
        return None
    return shiboken6.getCppPointer(screen)[0]


class WallpaperManager(QObject):
    interactiveChanged = Signal(bool)
    pausedChanged = Signal(bool)

    def __init__(self, profile, url: str, boot_script: str | None = None,
                 interactive: bool = False, audio_hub=None, show_wallpaper: bool = True, parent=None):
        super().__init__(parent)
        self._show_wallpaper = show_wallpaper  # False: yalnız ekran koruyucu, pencereler gizli ve dondurulmuş
        self._screensaver = False
        self._profile = profile
        self._audio_hub = audio_hub
        self._url = url
        self._boot_script = boot_script
        self._interactive = interactive
        self._paused = False
        self._disabled_screens: set[str] = set()
        self._adapter = detect()
        self._windows: list[_Entry] = []
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(_SCREEN_SETTLE_MS)
        self._sync_timer.timeout.connect(self._sync_screens)
        self._parked: dict[str, tuple[_Entry, float]] = {}
        self._parked_hosts: set[int] = set()
        self._streaming: dict[int, int] = {}  # id(host) → Canard.saver bağlantı sayısı
        self._park_timer = QTimer(self)
        self._park_timer.setInterval(60_000)
        self._park_timer.timeout.connect(self._expire_parked)
        self.occlusion_grace_ms = 0
        self._refreshing: set[int] = set()
        self._pending_pause: dict[int, QTimer] = {}
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._schedule_sync)
        app.screenRemoved.connect(self._on_screen_removed)

    def start(self, extra_hotkeys: dict | None = None) -> None:
        self._sync_screens()
        hotkeys = {
            "prev": lambda: self.send_key("ArrowLeft"),
            "next": lambda: self.send_key("ArrowRight"),
            "toggle_interactive": self.toggle_interactive,
            "toggle_pause": lambda: self.set_paused(not self._paused),
        }
        hotkeys.update(extra_hotkeys or {})
        try:
            registered = self._adapter.install_hotkeys(hotkeys)
        except (NotImplementedError, OSError) as error:
            hotkey_log.warning("global shortcuts could not be installed: %s", error)
        else:
            for line in registered:
                hotkey_log.info("%s", line)

    def close(self) -> None:
        self._sync_timer.stop()
        self._park_timer.stop()
        for entry in self._windows:
            self._destroy(entry, immediate=True)
        for entry, _since in self._parked.values():
            self._destroy(entry, immediate=True)
        self._windows.clear()
        self._parked.clear()

    def load(self, url: str, boot_script: str | None = None) -> None:
        self._url = url
        self._boot_script = boot_script
        self.reload()

    def reload(self) -> None:
        """Sayfalar yeniden yüklenir, park edilenler atılır (profil betikleri değişince de gerekir)."""
        self._drop_parked()
        for entry in self._windows:
            entry.host.set_boot_script(self._boot_script)
            entry.host.load(self._url)

    def _drop_parked(self) -> None:
        for entry, _since in self._parked.values():
            self._parked_hosts.discard(id(entry.host))
            self._destroy(entry)
        self._parked.clear()
        self._park_timer.stop()

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool) -> None:
        if paused == self._paused:
            return
        self._paused = paused
        for window, host, _token, _screen in self._windows:
            if id(host) in self._streaming:
                continue  # yayın bitince release_stream uygular
            self._cancel_pause(host)
            visible = window.isVisible()
            if visible and not paused:
                try:
                    visible = self._adapter.window_visible(window)
                except NotImplementedError:
                    pass
            host.set_suspended(paused or not visible)
        log.info("content %s", "paused (manually)" if paused else "resumed")
        self.pausedChanged.emit(paused)

    def set_disabled_screens(self, names) -> None:
        """Bu adlı ekranlarda içerik gösterilmez; sayfaları park edilir."""
        names = set(names)
        if names == self._disabled_screens:
            return
        self._disabled_screens = names
        self._sync_screens()

    def set_show_wallpaper(self, show: bool) -> None:
        """Gizliyken sayfalar yeniden yüklenmez, dondurulup yayın için hazır bekler."""
        if show == self._show_wallpaper:
            return
        self._show_wallpaper = show
        log.info("wallpaper %s", "on" if show else "off")
        if self._screensaver:
            return  # set_screensaver(False) uygular
        for entry in self._windows:
            window, host = entry.window, entry.host
            if id(host) in self._streaming:
                continue
            if show:
                window.show()
                self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
                host.set_suspended(self._paused)
            else:
                window.hide()
                host.set_suspended(True)
            self._apply_inset(entry)

    def _schedule_sync(self, *args) -> None:
        self._sync_timer.start()

    def _on_screen_removed(self, screen) -> None:
        # Pencere AppKit'in taşıma döngüsündeyken yok edilmez; kalan ekranı örtmesin diye yalnız saydamlaştırılır.
        key = _screen_key(screen)
        for entry in self._windows:
            if _screen_key(entry.screen) == key:
                entry.window.setWindowOpacity(0.0)
                self._parked_hosts.add(id(entry.host))
        self._schedule_sync()

    def _sync_screens(self) -> None:
        current = {_screen_key(screen): screen for screen in QGuiApplication.screens()
                   if screen.name() not in self._disabled_screens}
        for screen in current.values():
            g, a = screen.geometry(), screen.availableGeometry()
            screen_log.debug("%s: %dx%d @(%d,%d) dpr=%.1f, available top=%d", screen.name(), g.width(),
                             g.height(), g.x(), g.y(), screen.devicePixelRatio(), a.top() - g.top())
        kept = []
        for entry in self._windows:
            if _screen_key(entry.screen) in current:
                if entry.window.geometry() != entry.screen.geometry():
                    entry.window.setGeometry(entry.screen.geometry())
                self._apply_inset(entry)
                kept.append(entry)
            else:
                self._park(entry)
        known = {_screen_key(entry.screen) for entry in kept}
        for key, screen in current.items():
            if key in known:
                continue
            parked = self._parked.pop(screen.name(), None)
            if parked is not None:
                kept.append(self._restore(parked[0], screen))
            else:
                if self._windows:
                    screen_log.info("added: %s", screen.name())
                kept.append(self._create(screen))
        self._windows = kept

    def _park(self, entry: _Entry) -> None:
        name = entry.window.windowTitle().split(" — ")[-1]
        entry.window.hide()
        self._parked_hosts.add(id(entry.host))
        entry.host.set_suspended(True)
        previous = self._parked.pop(name, None)
        if previous is not None:
            self._destroy(previous[0])
        self._parked[name] = (entry, time.monotonic())
        self._park_timer.start()
        reason = "turned off in menu" if name in self._disabled_screens else "removed"
        screen_log.info("%s: %s (page parked for %d min)", reason, name, _PARK_SECONDS // 60)

    def _restore(self, entry: _Entry, screen) -> _Entry:
        window, host = entry.window, entry.host
        if window.windowHandle() is not None:
            window.windowHandle().setScreen(screen)
        window.setGeometry(screen.geometry())
        window.setWindowOpacity(1.0)
        self._parked_hosts.discard(id(host))
        if self._show_wallpaper or self._screensaver:
            window.show()
            self._apply_layer(window)  # show() pencere seviyesini sıfırlayabilir
            host.set_suspended(self._paused and not self._screensaver)
        self._watch_screen(screen)
        restored = entry._replace(screen=screen)
        self._apply_inset(restored)
        screen_log.info("back: %s (page resumes where it left off)", screen.name())
        return restored

    def _expire_parked(self) -> None:
        now = time.monotonic()
        for name, (entry, since) in list(self._parked.items()):
            if now - since >= _PARK_SECONDS:
                del self._parked[name]
                self._parked_hosts.discard(id(entry.host))
                self._destroy(entry)
                screen_log.info("park time over, page closed: %s", name)
        if not self._parked:
            self._park_timer.stop()

    def _create(self, screen) -> _Entry:
        window = QWidget(None, Qt.WindowType.FramelessWindowHint)
        window.setWindowTitle(f"Canard Wallpaper — {screen.name()}")
        window.setGeometry(screen.geometry())
        # Qt 6.9+ çentik güvenli alanını uygular; pencere menü çubuğu dahil tüm ekranı kaplamalı (boşluk: _apply_inset).
        window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        window.setAutoFillBackground(True)
        palette = window.palette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        window.setPalette(palette)
        host = WebHost(self._profile, boot_script=self._boot_script, audio_hub=self._audio_hub)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(host)
        host.load(self._url)
        token = None
        try:
            if self._show_wallpaper or self._screensaver:
                window.show()
                self._apply_layer(window)
                if self._paused and not self._screensaver:
                    host.set_suspended(True)
            else:
                window.winId()  # görünürlük gözlemi gizli pencerede de yerel pencere ister
                host.set_suspended(True)
            token = self._adapter.watch_visibility(
                window, lambda visible, h=host, n=screen.name(): self._on_visibility(h, n, visible)
            )
        except NotImplementedError:
            pass
        self._watch_screen(screen)
        entry = _Entry(window, host, token, screen)
        self._apply_inset(entry)
        return entry

    def _apply_layer(self, window) -> None:
        if self._screensaver:
            self._adapter.apply_screensaver_window(window)
        else:
            self._adapter.apply_wallpaper_window(window, interactive=self._interactive)

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._schedule_sync)
        screen.availableGeometryChanged.connect(self._schedule_sync)

    def _destroy(self, entry: _Entry, immediate: bool = False) -> None:
        self._cancel_pause(entry.host)
        if entry.token is not None:
            self._adapter.unwatch_visibility(entry.token)
        entry.window.close()
        if immediate:
            shiboken6.delete(entry.window)
        else:
            entry.window.deleteLater()

    def send_key(self, key: str) -> None:
        hotkey_log.debug("key to content: %s", key)
        for entry in self._windows:
            entry.host.send_key(key)

    def acquire_stream(self, display_id: int):
        """Canard.saver bağlanınca o ekranın sayfası örtülü/kilitliyken de canlı tutulur. (host, ad) ya da None."""
        for window, host, _token, screen in self._windows:
            try:
                if self._adapter.screen_display_id(screen) != display_id:
                    continue
            except NotImplementedError:
                return None
            self._streaming[id(host)] = self._streaming.get(id(host), 0) + 1
            if not window.isVisible():
                # Yalnız ekran koruyucu modu: kilit ekranının arkasında, masaüstü katmanında çizilir.
                window.show()
                self._adapter.apply_wallpaper_window(window, interactive=False)
            host.set_suspended(False)
            return host, screen.name()
        return None

    def release_stream(self, host) -> None:
        count = self._streaming.get(id(host), 0) - 1
        if count > 0:
            self._streaming[id(host)] = count
            return
        self._streaming.pop(id(host), None)
        for window, entry_host, _token, screen in self._windows:
            if entry_host is not host:
                continue
            if not self._show_wallpaper and not self._screensaver:
                window.hide()
                host.set_suspended(True)
            else:
                # Yayın sırasında yok sayılan görünürlük durumu şimdi uygulanır.
                try:
                    self._on_visibility(host, screen.name(), self._adapter.window_visible(window))
                except NotImplementedError:
                    pass

    def set_screensaver(self, active: bool) -> None:
        """Pencereleri ekran koruyucu katmanına çıkarır / geri indirir (sayfa yeniden yüklenmez)."""
        self._screensaver = active
        for entry in self._windows:
            window, host, _token, screen = entry
            if active:
                window.setGeometry(screen.geometry())
                if not window.isVisible():
                    window.show()
                self._adapter.apply_screensaver_window(window)
                host.set_suspended(False)
            elif self._show_wallpaper:
                self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
                host.set_suspended(self._paused)
            else:
                window.hide()
                host.set_suspended(True)
            self._apply_inset(entry)

    def toggle_interactive(self) -> None:
        self.set_interactive(not self._interactive)

    def set_interactive(self, interactive: bool) -> None:
        if interactive == self._interactive:
            return
        if self._screensaver:
            log.info("interactive mode cannot change while the screensaver is on")
            return
        if not self._show_wallpaper:
            log.info("interactive mode cannot change while the wallpaper is off")
            return
        self._interactive = interactive
        for entry in self._windows:
            self._adapter.apply_wallpaper_window(entry.window, interactive=interactive)
            self._apply_inset(entry)
        log.info("interactive mode: %s", "on" if interactive else "off")
        self.interactiveChanged.emit(interactive)

    def _apply_inset(self, entry: _Entry) -> None:
        # Etkileşimli modda içerik menü çubuğunun altından başlar ki üst menüleri tıklanabilsin.
        screen = entry.screen
        interactive = self._interactive and self._show_wallpaper and not self._screensaver
        top = max(0, screen.availableGeometry().top() - screen.geometry().top()) if interactive else 0
        log.debug("%s: top inset %d px", screen.name(), top)
        entry.window.layout().setContentsMargins(0, top, 0, 0)

    def _on_visibility(self, host: WebHost, screen_name: str, visible: bool) -> None:
        if id(host) in self._refreshing:
            return
        self._cancel_pause(host)
        if id(host) in self._parked_hosts:
            return  # AppKit'in taşıdığı, ekranı çıkarılmış pencere
        if id(host) in self._streaming:
            log.debug("%s: %s (Canard.saver streaming, page stays live)", screen_name,
                      "visible" if visible else "occluded")
            return
        if not visible and not self._show_wallpaper and not self._screensaver:
            return
        if self._paused and not self._screensaver:
            host.set_suspended(True)
            return
        if not visible and self.occlusion_grace_ms:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._pause(host, screen_name))
            timer.start(self.occlusion_grace_ms)
            self._pending_pause[id(host)] = timer
            log.debug("%s: occluded, pausing in %d ms (Canard.saver may connect)", screen_name,
                      self.occlusion_grace_ms)
            return
        host.set_suspended(not visible)
        if visible:
            QTimer.singleShot(_REFRESH_DELAY_MS, host, lambda: self._refresh_window(host, screen_name))
        log.info("%s: %s", screen_name, "resumed" if visible else "paused (occluded)")

    def _refresh_window(self, host: WebHost, screen_name: str) -> None:
        """Örtülü → görünür sonrası macOS pencerenin eski karesini gösterebiliyor: pencere yeniden sıralanarak tazelenir."""
        entry = next((e for e in self._windows if e.host is host), None)
        if (entry is None or host.suspended or id(host) in self._streaming or id(host) in self._parked_hosts
                or not self._adapter.window_visible(entry.window)):
            return
        self._refreshing.add(id(host))
        try:
            self._adapter.refresh_wallpaper_window(entry.window)
        except NotImplementedError:
            entry.window.hide()
            entry.window.show()
            self._adapter.apply_wallpaper_window(entry.window, interactive=self._interactive)
        QTimer.singleShot(_REFRESH_SETTLE_MS, host, lambda: self._refreshing.discard(id(host)))
        log.debug("%s: window refreshed", screen_name)

    def _pause(self, host: WebHost, screen_name: str) -> None:
        self._cancel_pause(host)
        if id(host) in self._streaming or id(host) in self._parked_hosts:
            return
        host.set_suspended(True)
        log.info("%s: paused (occluded)", screen_name)

    def _cancel_pause(self, host) -> None:
        timer = self._pending_pause.pop(id(host), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
