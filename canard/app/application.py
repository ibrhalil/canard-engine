import logging
import sys
import time

import canard
import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from canard.app.settings import SettingsStore, default_settings_path
from canard.content.server import ServerManager
from canard.content.sources import SourceRegistry
from canard.platform.base import detect
from canard.web.bridge import HostBridge
from canard.web.engine import WebHost, create_profile

log = logging.getLogger("canard.wallpaper")
screen_log = logging.getLogger("canard.screen")
hotkey_log = logging.getLogger("canard.hotkey")


# Ekran takılıp çıkarılınca AppKit pencereleri taşırken (_displayChangedSoAdjustWindows) pencere kapatmak
# libqcocoa'da SEGV'ye yol açtı (2026-11-21, harici ekran kablosu). Ekran olaylarına anında tepki verilmez;
# biriktirilip AppKit işini bitirdikten sonra uzlaştırılır.
_SCREEN_SETTLE_MS = 500
# Çıkarılan ekranın sayfası kapatılmaz, dondurulup park edilir: aynı ekran geri takılınca kaldığı yerden
# sürer (içeriğin sessionStorage'ı, ör. mscillus seçili modu, korunur). Bu süre içinde dönmezse atılır.
_PARK_SECONDS = 600


def _screen_key(screen) -> int | None:
    if screen is None or not shiboken6.isValid(screen):
        return None
    return shiboken6.getCppPointer(screen)[0]


class WallpaperManager(QObject):
    def __init__(self, profile, url: str, boot_script: str | None = None,
                 interactive: bool = False, audio_hub=None, show_wallpaper: bool = True, parent=None):
        super().__init__(parent)
        # show_wallpaper=False: yalnız ekran koruyucu — pencereler gizli ve dondurulmuş bekler.
        self._show_wallpaper = show_wallpaper
        self._screensaver = False
        self._profile = profile
        self._audio_hub = audio_hub
        self._url = url
        self._boot_script = boot_script
        self._interactive = interactive
        self._adapter = detect()
        self._windows: list[tuple[QWidget, WebHost, object, object]] = []
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(_SCREEN_SETTLE_MS)
        self._sync_timer.timeout.connect(self._sync_screens)
        self._parked: dict[str, tuple[tuple, float]] = {}
        self._parked_hosts: set[int] = set()
        self._streaming: dict[int, int] = {}  # id(host) → Canard.saver bağlantı sayısı
        self._park_timer = QTimer(self)
        self._park_timer.setInterval(60_000)
        self._park_timer.timeout.connect(self._expire_parked)
        app = QGuiApplication.instance()
        app.screenAdded.connect(self._schedule_sync)
        app.screenRemoved.connect(self._on_screen_removed)

    def start(self, extra_hotkeys: dict | None = None) -> None:
        self._sync_screens()
        hotkeys = {
            "prev": lambda: self.send_key("ArrowLeft"),
            "next": lambda: self.send_key("ArrowRight"),
        }
        if self._show_wallpaper:
            hotkeys["toggle_interactive"] = self.toggle_interactive
        hotkeys.update(extra_hotkeys or {})
        try:
            registered = self._adapter.install_hotkeys(hotkeys)
        except (NotImplementedError, OSError) as error:
            hotkey_log.warning("global kısayollar kurulamadı: %s", error)
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

    def _schedule_sync(self, *args) -> None:
        self._sync_timer.start()

    def _on_screen_removed(self, screen) -> None:
        # AppKit bu ekranın penceresini kalan ekrana taşır; uzlaştırmayı beklerken orayı örtmesin diye yalnız
        # saydamlığı sıfırlanır (pencere yok edilmez → AppKit'in taşıma döngüsünde güvenli).
        key = _screen_key(screen)
        for window, host, _token, entry_screen in self._windows:
            if _screen_key(entry_screen) == key:
                window.setWindowOpacity(0.0)
                self._parked_hosts.add(id(host))
        self._schedule_sync()

    def _sync_screens(self) -> None:
        # Yalnız değişen ekranlara dokunulur: kalan ekranların içeriği yeniden yüklenmez.
        current = {_screen_key(screen): screen for screen in QGuiApplication.screens()}
        for screen in current.values():
            g, a = screen.geometry(), screen.availableGeometry()
            screen_log.debug("%s: %dx%d @(%d,%d) dpr=%.1f, kullanılabilir üst=%d", screen.name(), g.width(),
                             g.height(), g.x(), g.y(), screen.devicePixelRatio(), a.top() - g.top())
        kept = []
        for entry in self._windows:
            window, _host, _token, screen = entry
            if _screen_key(screen) in current:
                if window.geometry() != screen.geometry():
                    window.setGeometry(screen.geometry())
                self._apply_inset(entry)
                kept.append(entry)
            else:
                self._park(entry)
        known = {_screen_key(entry[3]) for entry in kept}
        for key, screen in current.items():
            if key not in known:
                parked = self._parked.pop(screen.name(), None)
                if parked is not None:
                    kept.append(self._restore(parked[0], screen))
                else:
                    if self._windows:
                        screen_log.info("eklendi: %s", screen.name())
                    kept.append(self._create(screen))
        self._windows = kept

    def _park(self, entry) -> None:
        window, host, _token, _screen = entry
        name = window.windowTitle().split(" — ")[-1]
        window.hide()
        self._parked_hosts.add(id(host))
        host.set_suspended(True)
        previous = self._parked.pop(name, None)
        if previous is not None:
            self._destroy(previous[0])
        self._parked[name] = (entry, time.monotonic())
        self._park_timer.start()
        screen_log.info("kaldırıldı: %s (sayfa %d dk park edildi)", name, _PARK_SECONDS // 60)

    def _restore(self, entry, screen):
        window, host, token, _old_screen = entry
        if window.windowHandle() is not None:
            window.windowHandle().setScreen(screen)
        window.setGeometry(screen.geometry())
        window.setWindowOpacity(1.0)
        self._parked_hosts.discard(id(host))
        if self._show_wallpaper or self._screensaver:
            window.show()
            # Qt show() pencere seviyesini sıfırlayabilir: davranış yeniden uygulanır.
            if self._screensaver:
                self._adapter.apply_screensaver_window(window)
            else:
                self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
            host.set_suspended(False)
        screen.geometryChanged.connect(self._schedule_sync)
        screen.availableGeometryChanged.connect(self._schedule_sync)
        restored = (window, host, token, screen)
        self._apply_inset(restored)
        screen_log.info("geri geldi: %s (sayfa kaldığı yerden sürüyor)", screen.name())
        return restored

    def _expire_parked(self) -> None:
        now = time.monotonic()
        for name, (entry, since) in list(self._parked.items()):
            if now - since >= _PARK_SECONDS:
                del self._parked[name]
                self._parked_hosts.discard(id(entry[1]))
                self._destroy(entry)
                screen_log.info("park süresi doldu, sayfa kapatıldı: %s", name)
        if not self._parked:
            self._park_timer.stop()

    def _create(self, screen):
        window = QWidget(None, Qt.WindowType.FramelessWindowHint)
        window.setWindowTitle(f"Canard Wallpaper — {screen.name()}")
        window.setGeometry(screen.geometry())
        # Qt 6.9+ üst düzey pencerelerde çentik güvenli alanına uyar: çentikli ekranda içerik 38 pt aşağıdan
        # başlıyor, üstte siyah şerit kalıyor ve Canard.saver karesi ekranla aynı boyutta olmuyordu (2148 ≠ 2224).
        # Duvar kağıdı her ekranda menü çubuğunun arkası dahil tamamını kaplar; boşluğu yalnız _apply_inset verir.
        window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        # Etkileşimli modda menü çubuğu altında kalan şerit (bkz. _apply_inset) içerikle uyumlu koyu kalsın.
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
                if self._screensaver:
                    self._adapter.apply_screensaver_window(window)
                else:
                    self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
            else:
                window.winId()  # gizli de olsa yerel pencere: görünürlük gözlemi için gerekli
                host.set_suspended(True)
            token = self._adapter.watch_visibility(
                window, lambda visible, h=host, n=screen.name(): self._on_visibility(h, n, visible)
            )
        except NotImplementedError:
            pass
        screen.geometryChanged.connect(self._schedule_sync)
        screen.availableGeometryChanged.connect(self._schedule_sync)
        entry = (window, host, token, screen)
        self._apply_inset(entry)
        return entry

    def _destroy(self, entry, immediate: bool = False) -> None:
        window, _host, token, _screen = entry
        if token is not None:
            self._adapter.unwatch_visibility(token)
        window.close()
        if immediate:
            shiboken6.delete(window)
        else:
            window.deleteLater()

    def send_key(self, key: str) -> None:
        hotkey_log.debug("içeriğe tuş: %s", key)
        for _window, host, _token, _screen in self._windows:
            host.send_key(key)

    def acquire_stream(self, display_id: int):
        """Canard.saver bağlanınca: o ekranın sayfası örtülü/kilitliyken de canlı tutulur. (host, ad) ya da None."""
        for entry in self._windows:
            window, host, _token, screen = entry
            try:
                if self._adapter.screen_display_id(screen) != display_id:
                    continue
            except NotImplementedError:
                return None
            self._streaming[id(host)] = self._streaming.get(id(host), 0) + 1
            if not window.isVisible():
                # Yalnız ekran koruyucu modu: kilit ekranının arkasında, masaüstü katmanında görünür yapılır.
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
        for entry in self._windows:
            window, entry_host, _token, _screen = entry
            if entry_host is not host:
                continue
            if not self._show_wallpaper and not self._screensaver:
                window.hide()
                host.set_suspended(True)
            else:
                # Yayın sırasında yok sayılan görünürlük değişikliklerini telafi et.
                try:
                    host.set_suspended(not self._adapter.window_visible(window))
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
            else:
                window.hide()
                host.set_suspended(True)
            self._apply_inset(entry)

    def toggle_interactive(self) -> None:
        if self._screensaver:
            log.info("ekran koruyucu açıkken etkileşimli mod değişmez")
            return
        self._interactive = not self._interactive
        for entry in self._windows:
            self._adapter.apply_wallpaper_window(entry[0], interactive=self._interactive)
            self._apply_inset(entry)
        log.info("etkileşimli mod: %s", "açık" if self._interactive else "kapalı")

    def _apply_inset(self, entry) -> None:
        # Pencere menü çubuğunun arkası dahil tüm ekranı kaplar; etkileşimli modda içeriğin üst menüleri
        # macOS menü çubuğunun altında kalmasın diye içerik o ekranın menü çubuğu kadar aşağıdan başlar.
        window, _host, _token, screen = entry
        interactive = self._interactive and not self._screensaver
        top = screen.availableGeometry().top() - screen.geometry().top() if interactive else 0
        log.debug("%s: üst boşluk %d px", screen.name(), max(0, top))
        window.layout().setContentsMargins(0, max(0, top), 0, 0)

    def _on_visibility(self, host: WebHost, screen_name: str, visible: bool) -> None:
        if id(host) in self._parked_hosts:
            return  # ekranı çıkarılmış pencere: AppKit taşırken gelen görünürlük olayları yok sayılır
        if id(host) in self._streaming:
            log.debug("%s: %s (Canard.saver yayını sürüyor, sayfa canlı kalır)", screen_name,
                      "görünür" if visible else "örtülü")
            return
        if not visible and not self._show_wallpaper and not self._screensaver:
            return  # yalnız ekran koruyucu modunda gizli bekleyen pencere zaten dondurulmuş
        host.set_suspended(not visible)
        log.info("%s: %s", screen_name, "devam" if visible else "duraklatıldı (örtülü)")


class CanardApp:
    def __init__(self, settings_path=None):
        self.settings_path = settings_path or default_settings_path()
        self.store = SettingsStore(self.settings_path)
        self.registry = SourceRegistry(self.store)
        self.servers = ServerManager()

    def run(self, url: str, mode: str = "preview", boot_script: str | None = None,
            interactive: bool = False, system_audio: bool = True,
            screensaver_after: float | None = None, saver_stream: bool = True,
            saver_fps: int | None = None) -> int:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        app = QApplication(sys.argv[:1])
        app.setApplicationName("Canard")
        app.setOrganizationName("Canard")
        app.setApplicationVersion(canard.__version__)
        from canard.app.logs import install_qt_handler

        install_qt_handler()

        import signal

        signal.signal(signal.SIGINT, lambda *_: app.quit())
        signal.signal(signal.SIGTERM, lambda *_: app.quit())
        keepalive = QTimer(app)
        keepalive.timeout.connect(lambda: None)
        keepalive.start(250)

        profile = create_profile()
        active = self.registry.active()
        bridge = HostBridge(mode=mode, source=active.to_dict() if active else None, system_audio=system_audio)
        bridge.install(profile)

        audio_hub = None
        if system_audio:
            from canard.audio.hub import SystemAudioHub
            from canard.web import system_audio as system_audio_script

            audio_hub = SystemAudioHub(detect(), app)
            system_audio_script.install(profile)

        screensaver = None
        stream_server = None
        if mode in ("wallpaper", "screensaver"):
            manager = WallpaperManager(profile, url, boot_script=boot_script, interactive=interactive,
                                       audio_hub=audio_hub, show_wallpaper=mode == "wallpaper")
            extra_hotkeys = {}
            if screensaver_after is not None:
                from canard.app.screensaver import ScreensaverController

                # Kısayol, denetleyici oluşmadan kaydedilir; çağrıldığında denetleyiciye yönlenir.
                extra_hotkeys["screensaver_now"] = lambda: screensaver and screensaver.start_now()
            manager.start(extra_hotkeys)
            if screensaver_after is not None:
                try:
                    screensaver = ScreensaverController(manager, detect(), screensaver_after * 60, app)
                except NotImplementedError:
                    logging.getLogger("canard.screensaver").error("bu platformda ekran koruyucu henüz yok")
            if saver_stream and sys.platform == "darwin":
                from canard.app.saver_stream import DEFAULT_FPS, SaverStreamServer

                stream_server = SaverStreamServer(manager, detect(), fps=saver_fps or DEFAULT_FPS, parent=app)
        else:
            host = WebHost(profile, boot_script=boot_script, audio_hub=audio_hub)
            window = QMainWindow()
            window.setWindowTitle(f"Canard — {mode}")
            window.setCentralWidget(host)
            window.resize(1280, 800)
            host.load(url)
            window.show()

        code = app.exec()
        if stream_server is not None:
            stream_server.close()
        if screensaver is not None:
            screensaver.close()
        if audio_hub is not None:
            audio_hub.shutdown()
        # Sayfalar profilden önce silinmeli; yoksa Qt "Release of profile requested but WebEnginePage
        # still not deleted" uyarır ve profil (localStorage, çerezler) diske düzgün yazılmayabilir.
        if mode in ("wallpaper", "screensaver"):
            manager.close()
        else:
            shiboken6.delete(window)
        shiboken6.delete(profile)
        self.servers.stop_all()
        return code
