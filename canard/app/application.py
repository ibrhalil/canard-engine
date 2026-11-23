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
from canard.web import create_engine, default_engine
from canard.web.base import unload_pages
from canard.web.bridge import HostBridge

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
            saver_fps: int | None = None, tray: bool = False, engine: str | None = None) -> int:
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

        web_engine = create_engine(engine or default_engine())
        logging.getLogger("canard.web").info("web engine: %s (%s)", web_engine.label, web_engine.version())
        runtime = window = menu = None
        if mode == "preview" and not tray:
            window = self._preview(app, web_engine, source, system_audio)
        else:
            app.setQuitOnLastWindowClosed(False)
            from canard.app.runtime import Runtime

            if tray:
                from canard.app.tray import TrayMenu, runtime_options

                runtime = Runtime(self, web_engine, **runtime_options(self.store))
                menu = TrayMenu(self, runtime)
            else:
                runtime = Runtime(self, web_engine, wallpaper=mode == "wallpaper", interactive=interactive,
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
        # Sayfalar motordan önce silinmeli; yoksa Chromium profili (localStorage, çerezler) diske düzgün yazılmayabilir.
        if runtime is not None:
            runtime.close()  # Runtime motoru da kapatır (menüden değiştirilmiş olabilir)
        if window is not None:
            unload_pages([window.centralWidget()])
            shiboken6.delete(window)
            web_engine.close()
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

    def _preview(self, app, engine, source, system_audio: bool):
        url = self.resolve(source)
        bridge = HostBridge(mode="preview", source=source.to_dict(), system_audio=system_audio, renderer=engine.name)
        engine.set_scripts(bridge.script_source(), system_audio)
        audio_hub = None
        if system_audio:
            from canard.audio.hub import SystemAudioHub

            audio_hub = SystemAudioHub(detect(), app)
            app.aboutToQuit.connect(audio_hub.shutdown)
        host = engine.create_host(boot_script=source.boot_script, audio_hub=audio_hub)
        window = QMainWindow()
        window.setWindowTitle(f"Canard — preview ({engine.label})")
        window.setCentralWidget(host)
        window.resize(1280, 800)
        host.load(url)
        window.show()
        return window
