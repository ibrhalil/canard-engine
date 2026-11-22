import logging
import sys
import time
from pathlib import Path

import canard
import shiboken6
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from canard.app.settings import SettingsStore, default_settings_path
from canard.content.server import ServerManager
from canard.content.sources import SourceError, SourceRegistry, resolve_load_url
from canard.platform.base import detect
from canard.web.bridge import HostBridge
from canard.web.engine import WebHost, create_profile

log = logging.getLogger("canard.wallpaper")
screen_log = logging.getLogger("canard.screen")
hotkey_log = logging.getLogger("canard.hotkey")
source_log = logging.getLogger("canard.source")


# Ekran takılıp çıkarılınca AppKit pencereleri taşırken (_displayChangedSoAdjustWindows) pencere kapatmak
# libqcocoa'da SEGV'ye yol açtı (2026-11-21, harici ekran kablosu). Ekran olaylarına anında tepki verilmez;
# biriktirilip AppKit işini bitirdikten sonra uzlaştırılır.
_SCREEN_SETTLE_MS = 500
# Çıkarılan ekranın sayfası kapatılmaz, dondurulup park edilir: aynı ekran geri takılınca kaldığı yerden
# sürer (içeriğin sessionStorage'ı, ör. mscillus seçili modu, korunur). Bu süre içinde dönmezse atılır.
_PARK_SECONDS = 600
# Örtülü → görünür sonrası pencere tazelenmeden önce beklenen süre ve tazelemenin kendi görünürlük olaylarının
# yok sayıldığı süre (bkz. _refresh_window).
_REFRESH_DELAY_MS = 250
_REFRESH_SETTLE_MS = 500
# Canard.saver yayını açıkken "örtülü" olayında sayfa hemen dondurulmaz: ekran koruyucu başlayınca macOS duvar kağıdını
# önce örtülü bildirir, Canard.saver ~1 sn sonra bağlanır (2026-11-22 sahip testi: 14:59:48 örtülü → 14:59:49 bağlandı).
# Hemen dondurmak sistem sesini ~1 sn kesiyordu; bu sürede bağlanırsa sayfa hiç durmaz.
OCCLUSION_GRACE_MS = 3000


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
        # show_wallpaper=False: yalnız ekran koruyucu — pencereler gizli ve dondurulmuş bekler.
        self._show_wallpaper = show_wallpaper
        self._screensaver = False
        self._profile = profile
        self._audio_hub = audio_hub
        self._url = url
        self._boot_script = boot_script
        self._interactive = interactive
        self._paused = False  # elle duraklatma (⌃⌥⌘P): görünürken de dondurulur; Canard.saver yayını canlandırır
        self._disabled_screens: set[str] = set()  # kullanıcının kapattığı ekranlar (ada göre): içerik yok
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
        self.occlusion_grace_ms = 0  # Runtime, yayın açıkken OCCLUSION_GRACE_MS verir
        self._refreshing: set[int] = set()  # pencere tazelenirken (id(host)), bkz. _refresh_window
        self._pending_pause: dict[int, QTimer] = {}  # id(host) → bekleyen dondurma
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

    def load(self, url: str, boot_script: str | None = None) -> None:
        """Tüm ekranlarda yeni içerik (ya da yeni boot script). Gizli/dondurulmuş sayfa görünür olunca yüklenir."""
        self._url = url
        self._boot_script = boot_script
        self.reload()

    def reload(self) -> None:
        """Profil betikleri (köprü, sistem sesi) değişince: sayfalar yeniden yüklenir, park edilenler atılır."""
        self._drop_parked()
        for _window, host, _token, _screen in self._windows:
            host.set_boot_script(self._boot_script)
            host.load(self._url)

    def _drop_parked(self) -> None:
        for entry, _since in self._parked.values():
            self._parked_hosts.discard(id(entry[1]))
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
                continue  # yayın sürüyor: bitince release_stream uygular
            self._cancel_pause(host)
            visible = window.isVisible()
            if visible and not paused:
                try:
                    visible = self._adapter.window_visible(window)
                except NotImplementedError:
                    pass
            host.set_suspended(paused or not visible)
        log.info("içerik %s", "duraklatıldı (elle)" if paused else "devam ediyor")
        self.pausedChanged.emit(paused)

    def set_disabled_screens(self, names) -> None:
        """Bu adlı ekranlarda içerik gösterilmez (duvar kağıdı ve Canard.saver). Sayfaları park edilir: ekran yeniden
        açılırsa kaldığı yerden sürer."""
        names = set(names)
        if names == self._disabled_screens:
            return
        self._disabled_screens = names
        self._sync_screens()

    def set_show_wallpaper(self, show: bool) -> None:
        """Duvar kağıdını gösterir / gizler; sayfalar yeniden yüklenmez (gizliyken dondurulur, yayın için hazır)."""
        if show == self._show_wallpaper:
            return
        self._show_wallpaper = show
        log.info("duvar kağıdı %s", "açık" if show else "kapalı")
        if self._screensaver:
            return  # boşta tam ekran bitince set_screensaver(False) uygular
        for entry in self._windows:
            window, host, _token, _screen = entry
            if id(host) in self._streaming:
                continue  # Canard.saver yayını sürüyor: bitince release_stream uygular
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
        current = {_screen_key(screen): screen for screen in QGuiApplication.screens()
                   if screen.name() not in self._disabled_screens}
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
        reason = "menüden kapatıldı" if name in self._disabled_screens else "kaldırıldı"
        screen_log.info("%s: %s (sayfa %d dk park edildi)", reason, name, _PARK_SECONDS // 60)

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
            host.set_suspended(self._paused and not self._screensaver)
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
                    if self._paused:
                        host.set_suspended(True)
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
        window, host, token, _screen = entry
        self._cancel_pause(host)
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
            window, entry_host, _token, screen = entry
            if entry_host is not host:
                continue
            if not self._show_wallpaper and not self._screensaver:
                window.hide()
                host.set_suspended(True)
            else:
                # Yayın sırasında yok sayılan görünürlük değişikliklerini telafi et. Ekran koruyucu kapanırken pencere
                # bir an daha örtülü görünür; aynı bekleme uygulanır, görünür olunca dondurma iptal edilir.
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
            log.info("ekran koruyucu açıkken etkileşimli mod değişmez")
            return
        if not self._show_wallpaper:
            log.info("duvar kağıdı kapalıyken etkileşimli mod değişmez")
            return
        self._interactive = interactive
        for entry in self._windows:
            self._adapter.apply_wallpaper_window(entry[0], interactive=interactive)
            self._apply_inset(entry)
        log.info("etkileşimli mod: %s", "açık" if interactive else "kapalı")
        self.interactiveChanged.emit(interactive)

    def _apply_inset(self, entry) -> None:
        # Pencere menü çubuğunun arkası dahil tüm ekranı kaplar; etkileşimli modda içeriğin üst menüleri
        # macOS menü çubuğunun altında kalmasın diye içerik o ekranın menü çubuğu kadar aşağıdan başlar.
        window, _host, _token, screen = entry
        interactive = self._interactive and self._show_wallpaper and not self._screensaver
        top = screen.availableGeometry().top() - screen.geometry().top() if interactive else 0
        log.debug("%s: üst boşluk %d px", screen.name(), max(0, top))
        window.layout().setContentsMargins(0, max(0, top), 0, 0)

    def _on_visibility(self, host: WebHost, screen_name: str, visible: bool) -> None:
        if id(host) in self._refreshing:
            return  # _refresh_window'un kendi görünürlük olayları
        self._cancel_pause(host)
        if id(host) in self._parked_hosts:
            return  # ekranı çıkarılmış pencere: AppKit taşırken gelen görünürlük olayları yok sayılır
        if id(host) in self._streaming:
            log.debug("%s: %s (Canard.saver yayını sürüyor, sayfa canlı kalır)", screen_name,
                      "görünür" if visible else "örtülü")
            return
        if not visible and not self._show_wallpaper and not self._screensaver:
            return  # yalnız ekran koruyucu modunda gizli bekleyen pencere zaten dondurulmuş
        if self._paused and not self._screensaver:
            host.set_suspended(True)  # elle duraklatılmış: görünür olması sayfayı canlandırmaz
            return
        if not visible and self.occlusion_grace_ms:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._pause(host, screen_name))
            timer.start(self.occlusion_grace_ms)
            self._pending_pause[id(host)] = timer
            log.debug("%s: örtülü, %d ms sonra duraklatılacak (Canard.saver bağlanabilir)", screen_name,
                      self.occlusion_grace_ms)
            return
        host.set_suspended(not visible)
        if visible:
            QTimer.singleShot(_REFRESH_DELAY_MS, host, lambda: self._refresh_window(host, screen_name))
        log.info("%s: %s", screen_name, "devam" if visible else "duraklatıldı (örtülü)")

    def _refresh_window(self, host: WebHost, screen_name: str) -> None:
        """Örtülü → görünür sonrası pencerenin ekrandaki görüntüsünü tazeler. 2026-11-22 sahip testi: IntelliJ öne gelip
        gidince duvar kağıdı ekranda son karede kalıyordu; sayfa, ses ve Qt çizimi sağlamdı (600 ms'de 37 kare,
        exposed=True, Canard.saver yayını aynı anda canlı), yalnız macOS pencerenin eski görüntüsünü gösteriyordu.
        ⌃⌥⌘H (pencere gizle/göster) düzeltiyordu; burada kendiliğinden yapılır: macOS'ta yalnız AppKit düzeyinde
        yeniden sıralama (titremesiz), yoksa Qt gizle/göster."""
        entry = next((e for e in self._windows if e[1] is host), None)
        if (entry is None or host._suspended or id(host) in self._streaming or id(host) in self._parked_hosts
                or not self._adapter.window_visible(entry[0])):
            return
        window = entry[0]
        self._refreshing.add(id(host))
        try:
            self._adapter.refresh_wallpaper_window(window)
        except NotImplementedError:
            window.hide()
            window.show()
            self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
        QTimer.singleShot(_REFRESH_SETTLE_MS, host, lambda: self._refreshing.discard(id(host)))
        log.debug("%s: pencere tazelendi", screen_name)

    def _pause(self, host: WebHost, screen_name: str) -> None:
        self._cancel_pause(host)
        if id(host) in self._streaming or id(host) in self._parked_hosts:
            return
        host.set_suspended(True)
        log.info("%s: duraklatıldı (örtülü)", screen_name)

    def _cancel_pause(self, host) -> None:
        timer = self._pending_pause.pop(id(host), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()


class CanardApp:
    def __init__(self, settings_path=None):
        self.settings_path = settings_path or default_settings_path()
        self.store = SettingsStore(self.settings_path)
        self.registry = SourceRegistry(self.store)
        self.servers = ServerManager()

    def resolve(self, source) -> str:
        """Kaynağın yüklenecek URL'si (yerel build için sunucu başlatılır). Geçersizse SourceError."""
        previous_port = source.port
        url = resolve_load_url(source, self.servers)
        if source.port != previous_port:
            if previous_port:
                source_log.warning("port %s dolu, %s kullanılıyor (içeriğin yerel verileri sıfırlanır)",
                            previous_port, source.port)
            self.registry.update(source)
        return url

    def run(self, source, mode: str = "preview", interactive: bool = False, system_audio: bool = True,
            screensaver_after: float | None = None, saver_stream: bool = True,
            saver_fps: int | None = None, tray: bool = False) -> int:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        try:
            detect().before_gui()
        except NotImplementedError:
            pass
        app = QApplication(sys.argv[:1])
        # Qt, QCoreApplication oluşurken yerel ayarı LANG'den alır (setlocale(LC_ALL, "")); belgesi sayı biçimini "C"ye
        # geri almayı önerir. tr_TR'de ondalık ayıracı virgül ve macOS menü çizimi bununla bozuluyor: ✓ sütunu,
        # kısayol ön ekleri (⌃⌥⌘) ve alt menü oku kayboluyor (2026-11-22, Qt'siz yalnız setlocale ile de tekrarlandı).
        import locale

        locale.setlocale(locale.LC_NUMERIC, "C")
        app.setApplicationName("Canard")
        app.setOrganizationName("Canard")
        app.setApplicationVersion(canard.__version__)
        # Qt pencereleri ve iletişim kutuları (macOS'ta NSApp ikonu) Python roketi yerine Canard ikonunu göstersin.
        # macOS biçimi Apple ızgarasında (kenar boşluklu), diğerleri tuvali dolduran biçim.
        icon = "canard-macos.svg" if sys.platform == "darwin" else "canard.svg"
        app.setWindowIcon(QIcon(str(Path(canard.__file__).parent / "resources" / icon)))
        if tray:
            try:
                detect().app_integration().set_accessory()  # Dock simgesi hiç görünmesin diye en başta
            except NotImplementedError:
                pass
        from canard.app.logs import install_qt_handler

        install_qt_handler()

        import signal

        signal.signal(signal.SIGINT, lambda *_: app.quit())
        signal.signal(signal.SIGTERM, lambda *_: app.quit())
        keepalive = QTimer(app)
        keepalive.timeout.connect(lambda: None)
        keepalive.start(250)

        profile = create_profile()
        runtime = window = menu = None
        if mode == "preview" and not tray:
            window = self._preview(app, profile, source, system_audio)
        else:
            # Duvar kağıdı gizliyken (ekran koruyucu modu) ya da menüdeki iletişim kutusu kapanınca çıkılmasın.
            app.setQuitOnLastWindowClosed(False)
            from canard.app.runtime import Runtime

            if tray:
                from canard.app.tray import TrayMenu, runtime_options

                runtime = Runtime(self, profile, **runtime_options(self.store))
                menu = TrayMenu(self, runtime)
            else:
                runtime = Runtime(self, profile, wallpaper=mode == "wallpaper", interactive=interactive,
                                  system_audio=system_audio, saver_stream=saver_stream, saver_fps=saver_fps,
                                  screensaver_after=screensaver_after)
            if source is not None:
                try:
                    runtime.set_source(source)
                except SourceError as error:
                    if menu is None:
                        raise
                    menu.show_error(f"Kaynak açılamadı: {error}")

        code = app.exec()
        if menu is not None:
            menu.close()
        # Sayfalar profilden önce silinmeli; yoksa Qt "Release of profile requested but WebEnginePage
        # still not deleted" uyarır ve profil (localStorage, çerezler) diske düzgün yazılmayabilir.
        if runtime is not None:
            runtime.close()
        if window is not None:
            shiboken6.delete(window)
        shiboken6.delete(profile)
        self.servers.stop_all()
        return code

    def _preview(self, app, profile, source, system_audio: bool):
        url = self.resolve(source)
        bridge = HostBridge(mode="preview", source=source.to_dict(), system_audio=system_audio)
        bridge.install(profile)
        audio_hub = None
        if system_audio:
            from canard.audio.hub import SystemAudioHub
            from canard.web import system_audio as system_audio_script

            audio_hub = SystemAudioHub(detect(), app)
            system_audio_script.install(profile)
            app.aboutToQuit.connect(audio_hub.shutdown)
        host = WebHost(profile, boot_script=source.boot_script, audio_hub=audio_hub)
        window = QMainWindow()
        window.setWindowTitle("Canard — preview")
        window.setCentralWidget(host)
        window.resize(1280, 800)
        host.load(url)
        window.show()
        return window
