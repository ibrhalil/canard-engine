import locale
import logging
import signal
import sys
from pathlib import Path

import shiboken6
from PySide6.QtCore import QLibraryInfo, Qt, QTimer, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

import canard
from canard import i18n
from canard.app.logs import install_qt_handler
from canard.app.settings import SettingsStore, default_settings_path
from canard.content.server import ServerManager
from canard.content.sources import SourceError, SourceRegistry, resolve_load_url
from canard.i18n import t
from canard.platform.base import detect
from canard.web.bridge import HostBridge
from canard.web.engine import WebHost, create_profile

source_log = logging.getLogger("canard.source")

LANGUAGE_KEY = "app.language"


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
                source_log.warning("port %s is busy, using %s (the content's local data starts empty)",
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
        # Qt yerel ayarı LANG'den alır; tr_TR sayı biçimi macOS menü çizimini bozuyor (✓, ⌃⌥⌘, alt menü oku).
        locale.setlocale(locale.LC_NUMERIC, "C")
        app.setApplicationName("Canard")
        app.setOrganizationName("Canard")
        app.setApplicationVersion(canard.__version__)
        icon = "canard-macos.svg" if sys.platform == "darwin" else "canard.svg"
        app.setWindowIcon(QIcon(str(Path(canard.__file__).parent / "resources" / icon)))
        if tray:
            try:
                detect().app_integration().set_accessory()  # Dock simgesi hiç görünmesin
            except NotImplementedError:
                pass
        install_qt_handler()
        self._qt_translator = None
        self.apply_qt_language()
        signal.signal(signal.SIGINT, lambda *_: app.quit())
        signal.signal(signal.SIGTERM, lambda *_: app.quit())
        keepalive = QTimer(app)  # Python sinyal işleyicileri Qt döngüsü dönerken çalışabilsin
        keepalive.timeout.connect(lambda: None)
        keepalive.start(250)

        profile = create_profile()
        runtime = window = menu = None
        if mode == "preview" and not tray:
            window = self._preview(app, profile, source, system_audio)
        else:
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
                    menu.show_error(t("Could not open source: {error}", error=error))

        code = app.exec()
        if menu is not None:
            menu.close()
        # Sayfalar profilden önce silinmeli; yoksa profil (localStorage, çerezler) diske düzgün yazılmayabilir.
        if runtime is not None:
            runtime.close()
        if window is not None:
            shiboken6.delete(window)
        shiboken6.delete(profile)
        self.servers.stop_all()
        return code

    def apply_qt_language(self) -> None:
        """Qt'nin kendi metinleri (standart düğmeler vb.) de seçilen dilde olsun."""
        app = QApplication.instance()
        if self._qt_translator is not None:
            app.removeTranslator(self._qt_translator)
            self._qt_translator = None
        if i18n.language == "en":
            return
        translator = QTranslator(app)
        if translator.load(f"qtbase_{i18n.language}", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
            self._qt_translator = translator

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
