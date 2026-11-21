import sys

import canard
import shiboken6
from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget

from canard.app.settings import SettingsStore, default_settings_path
from canard.content.server import ServerManager
from canard.content.sources import SourceRegistry
from canard.platform.base import detect
from canard.web.bridge import HostBridge
from canard.web.engine import WebHost, create_profile


class WallpaperManager(QObject):
    def __init__(self, profile, url: str, boot_script: str | None = None,
                 interactive: bool = False, audio_hub=None, parent=None):
        super().__init__(parent)
        self._profile = profile
        self._audio_hub = audio_hub
        self._url = url
        self._boot_script = boot_script
        self._interactive = interactive
        self._adapter = detect()
        self._windows: list[tuple[QWidget, WebHost, object]] = []
        app = QGuiApplication.instance()
        app.screenAdded.connect(self.rebuild)
        app.screenRemoved.connect(self.rebuild)

    def start(self) -> None:
        self.rebuild()
        try:
            registered = self._adapter.install_hotkeys({
                "prev": lambda: self.send_key("ArrowLeft"),
                "next": lambda: self.send_key("ArrowRight"),
                "toggle_interactive": self.toggle_interactive,
            })
        except (NotImplementedError, OSError) as error:
            print(f"Global kısayollar kurulamadı: {error}", file=sys.stderr)
        else:
            for line in registered:
                print(f"Kısayol: {line}")

    def close(self) -> None:
        self._teardown(immediate=True)

    def _teardown(self, immediate: bool = False) -> None:
        for window, _host, token in self._windows:
            if token is not None:
                self._adapter.unwatch_visibility(token)
            window.close()
            if immediate:
                shiboken6.delete(window)
            else:
                window.deleteLater()
        self._windows.clear()

    def rebuild(self, *args) -> None:
        self._teardown()
        for screen in QGuiApplication.screens():
            window = QWidget(None, Qt.WindowType.FramelessWindowHint)
            window.setWindowTitle(f"Canard Wallpaper — {screen.name()}")
            window.setGeometry(screen.geometry())
            host = WebHost(self._profile, boot_script=self._boot_script, audio_hub=self._audio_hub)
            layout = QVBoxLayout(window)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(host)
            host.load(self._url)
            window.show()
            token = None
            try:
                self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
                token = self._adapter.watch_visibility(
                    window, lambda visible, h=host, n=screen.name(): self._on_visibility(h, n, visible)
                )
            except NotImplementedError:
                pass
            self._windows.append((window, host, token))

    def send_key(self, key: str) -> None:
        for _window, host, _token in self._windows:
            host.send_key(key)

    def toggle_interactive(self) -> None:
        self._interactive = not self._interactive
        for window, _host, _token in self._windows:
            self._adapter.apply_wallpaper_window(window, interactive=self._interactive)
        print("Etkileşimli mod: " + ("açık" if self._interactive else "kapalı"), flush=True)

    def _on_visibility(self, host: WebHost, screen_name: str, visible: bool) -> None:
        host.set_suspended(not visible)
        print(f"[{screen_name}] " + ("devam" if visible else "duraklatıldı (örtülü)"), flush=True)


class CanardApp:
    def __init__(self, settings_path=None):
        self.settings_path = settings_path or default_settings_path()
        self.store = SettingsStore(self.settings_path)
        self.registry = SourceRegistry(self.store)
        self.servers = ServerManager()

    def run(self, url: str, mode: str = "preview", boot_script: str | None = None,
            interactive: bool = False, system_audio: bool = True) -> int:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
        app = QApplication(sys.argv[:1])
        app.setApplicationName("Canard")
        app.setOrganizationName("Canard")
        app.setApplicationVersion(canard.__version__)

        import signal

        from PySide6.QtCore import QTimer

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

        if mode == "wallpaper":
            manager = WallpaperManager(profile, url, boot_script=boot_script, interactive=interactive,
                                       audio_hub=audio_hub)
            manager.start()
        else:
            host = WebHost(profile, boot_script=boot_script, audio_hub=audio_hub)
            window = QMainWindow()
            window.setWindowTitle(f"Canard — {mode}")
            window.setCentralWidget(host)
            window.resize(1280, 800)
            host.load(url)
            window.show()

        code = app.exec()
        if audio_hub is not None:
            audio_hub.shutdown()
        # Sayfalar profilden önce silinmeli; yoksa Qt "Release of profile requested but WebEnginePage
        # still not deleted" uyarır ve profil (localStorage, çerezler) diske düzgün yazılmayabilir.
        if mode == "wallpaper":
            manager.close()
        else:
            shiboken6.delete(window)
        shiboken6.delete(profile)
        self.servers.stop_all()
        return code
