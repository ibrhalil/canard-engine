"""Çalışma zamanı: duvar kağıdı, Canard.saver yayını ve çalışırken değişebilen ayarlar (CLI ve menü çubuğu ortak)."""

import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal

from canard.app.saver_stream import DEFAULT_FPS
from canard.app.wallpaper import OCCLUSION_GRACE_MS, WallpaperManager
from canard.audio.hub import SystemAudioHub
from canard.content.sources import SourceError
from canard.i18n import t
from canard.platform.base import detect
from canard.web import create_engine
from canard.web.base import WebEngine, media_listeners
from canard.web.bridge import HostBridge

log = logging.getLogger("canard.runtime")


class Runtime(QObject):
    changed = Signal()
    error = Signal(str)  # kısayolla tetiklenen eylem başarısız olunca (menü çubuğu iletişim kutusuyla gösterir)
    permission_needed = Signal(str)  # "system_audio" | "microphone": içerik istedi ama macOS izni kapalı

    def __init__(self, app, engine: WebEngine, *, wallpaper: bool = True, interactive: bool = False,
                 system_audio: bool = True, saver_stream: bool = True, saver_fps: int | None = None,
                 screensaver_after: float | None = None, disabled_screens=(), parent=None):
        super().__init__(parent)
        self._app = app  # CanardApp
        self._engine = engine  # Runtime'ındır: close() ve set_engine() kapatır
        self._adapter = detect()
        self.wallpaper = wallpaper
        self.interactive = interactive
        self.system_audio = system_audio
        self.saver_stream = saver_stream and sys.platform == "darwin"
        self.saver_fps = saver_fps or DEFAULT_FPS
        self._screensaver_after = screensaver_after
        self.saver_off_hint = t("To turn it on, start Canard without --no-saver-stream")
        self.disabled_screens: set[str] = set(disabled_screens)
        self.source = None
        self.url = None
        # Yakalama yalnız bir sayfa ses isteyince başlar; sistem sesi kapalıyken köprü betiği eklenmez.
        self._audio_hub = SystemAudioHub(self._adapter, self)
        self._audio_hub.started.connect(lambda: self._check_permission("system_audio"))
        self.mic_requested = False
        media_listeners.append(self._on_media_request)
        self._manager = None
        self._stream = None
        self._screensaver = None
        self._install_scripts()

    @property
    def engine(self) -> WebEngine:
        return self._engine

    @property
    def running(self) -> bool:
        return self._manager is not None

    @property
    def paused(self) -> bool:
        return self._manager is not None and self._manager.paused

    def set_source(self, source) -> None:
        """Kaynağı açar ve aktif yapar. Yüklenemezse SourceError; o durumda önceki içerik sürer."""
        url = self._app.resolve(source)
        self._app.registry.set_active(source.id)
        self.source, self.url = source, url
        self._install_scripts()
        if self._manager is None:
            self._start()
        else:
            self._manager.load(url, source.boot_script)
        log.info("source: %s (%s) · %s%s", source.name, source.type, url,
                 " · boot script" if source.boot_script else "")
        self.changed.emit()

    def set_boot_script(self, boot_script: str | None) -> None:
        if self.source is None:
            return
        self.source.boot_script = boot_script
        self._app.registry.update(self.source)
        self._install_scripts()
        if self._manager is not None:
            self._manager.load(self.url, boot_script)
        log.info("boot script %s", "attached" if boot_script else "removed")
        self.changed.emit()

    def set_wallpaper(self, enabled: bool) -> None:
        self.wallpaper = enabled
        self._install_scripts()  # window.canard.mode sonraki yüklemede güncellenir
        if self._manager is not None:
            self._manager.set_show_wallpaper(enabled)
        self.changed.emit()

    def set_screen_enabled(self, name: str, enabled: bool) -> None:
        if enabled:
            self.disabled_screens.discard(name)
        else:
            self.disabled_screens.add(name)
        if self._manager is not None:
            self._manager.set_disabled_screens(self.disabled_screens)
        log.info("screen %s: %s", "on" if enabled else "off", name)
        self.changed.emit()

    def send_key(self, key: str) -> None:
        if self._manager is not None:
            self._manager.send_key(key)

    def toggle_wallpaper(self) -> None:
        self.set_wallpaper(not self.wallpaper)

    def set_paused(self, paused: bool) -> None:
        if self._manager is not None:
            self._manager.set_paused(paused)  # → changed

    def reload(self) -> None:
        if self._manager is not None:
            log.info("reloading page")
            self._manager.reload()

    def next_source(self) -> None:
        sources = self._app.registry.list()
        if not sources:
            return
        ids = [source.id for source in sources]
        current = ids.index(self.source.id) if self.source is not None and self.source.id in ids else -1
        target = sources[(current + 1) % len(sources)]
        if self.source is not None and target.id == self.source.id:
            return  # tek kaynak
        try:
            self.set_source(target)
        except SourceError as error:
            log.warning("could not open source: %s", error)
            self.error.emit(t("Could not open source ({name}): {error}", name=target.name, error=error))

    def set_interactive(self, enabled: bool) -> None:
        if self._manager is None:
            self.interactive = enabled
            self.changed.emit()
        else:
            self._manager.set_interactive(enabled)  # → _on_interactive

    def set_system_audio(self, enabled: bool) -> None:
        self.system_audio = enabled
        self._install_scripts()
        if self._manager is not None:
            self._manager.reload()
        log.info("system audio bridge %s", "on" if enabled else "off")
        self.changed.emit()

    def set_engine(self, name: str) -> None:
        """Web motorunu değiştirir: sayfalar yeni motorla yeniden açılır. İçerik verileri (localStorage vb.) motorlar
        arasında taşınmaz. Açılamazsa önceki motor sürer, hata `error` ile bildirilir."""
        if name == self._engine.name:
            return
        try:
            engine = create_engine(name)
        except Exception as error:
            log.error("web engine %s could not be started: %s", name, error, exc_info=True)
            self.error.emit(t("Could not switch the web engine: {error}", error=error))
            return
        previous, self._engine = self._engine, engine
        if self._stream is not None:
            self._stream.drop_sessions()  # yayınlanan sayfalar kapanacak; Canard.saver yeniden bağlanır
        self._install_scripts()
        if self._manager is not None:
            self._manager.set_engine(engine)
        previous.close()
        log.info("web engine: %s (%s)", engine.label, engine.version())
        self.changed.emit()

    def set_saver_stream(self, enabled: bool) -> None:
        self.saver_stream = enabled and sys.platform == "darwin"
        if self._stream is not None:
            self._stream.set_enabled(self.saver_stream)
        if self._manager is not None:
            self._manager.occlusion_grace_ms = OCCLUSION_GRACE_MS if self.saver_stream else 0
        self.changed.emit()

    def set_saver_fps(self, fps: int) -> None:
        self.saver_fps = fps
        if self._stream is not None:
            self._stream.set_fps(fps)
        log.info("screensaver frame rate: %d (for new connections)", fps)
        self.changed.emit()

    def start_screensaver(self) -> None:
        """⌃⌥⌘L / menü: --screensaver-after verildiyse Canard'ın kendi boşta tam ekranı, yoksa sistem ekran koruyucusu."""
        if self._screensaver is not None:
            self._screensaver.start_now()
            return
        try:
            self._adapter.app_integration().start_screensaver()
        except NotImplementedError:
            log.warning("cannot start the screensaver on this platform")
            return
        log.info("screensaver started")

    def permission_status(self, kind: str) -> str | None:
        try:
            return self._adapter.app_integration().permission_status(kind)
        except NotImplementedError:
            return None

    def _on_media_request(self, kind: str) -> None:
        self.mic_requested = True
        self._check_permission(kind)

    def _check_permission(self, kind: str, retry: bool = True) -> None:
        status = self.permission_status(kind)
        if status == "denied":
            log.warning("%s permission denied: the content cannot receive this audio", kind)
            self.permission_needed.emit(kind)
        elif status == "unknown" and retry:
            # İlk kullanımda macOS kendi izin penceresini açar; kullanıcı yanıtladıktan sonra yeniden bakılır.
            QTimer.singleShot(8000, lambda: self._check_permission(kind, retry=False))

    def close(self) -> None:
        if self._on_media_request in media_listeners:
            media_listeners.remove(self._on_media_request)
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._screensaver is not None:
            self._screensaver.close()
            self._screensaver = None
        self._audio_hub.shutdown()
        if self._manager is not None:
            self._manager.close()
            self._manager = None
        self._engine.close()  # sayfalardan sonra

    def _install_scripts(self) -> None:
        mode = "wallpaper" if self.wallpaper else "screensaver"
        source = self.source.to_dict() if self.source else None
        bridge = HostBridge(mode=mode, source=source, system_audio=self.system_audio, renderer=self._engine.name)
        self._engine.set_scripts(bridge.script_source(), self.system_audio)

    def _start(self) -> None:
        manager = self._manager = WallpaperManager(
            self._engine, self.url, boot_script=self.source.boot_script, interactive=self.interactive,
            audio_hub=self._audio_hub, show_wallpaper=self.wallpaper, parent=self)
        manager.set_disabled_screens(self.disabled_screens)
        manager.interactiveChanged.connect(self._on_interactive)
        manager.pausedChanged.connect(lambda _paused: self.changed.emit())
        manager.occlusion_grace_ms = OCCLUSION_GRACE_MS if self.saver_stream else 0
        manager.start({
            "screensaver_now": self.start_screensaver,
            "toggle_wallpaper": self.toggle_wallpaper,
            "reload": self.reload,
            "next_source": self.next_source,
        })
        if self._screensaver_after is not None:
            from canard.app.screensaver import ScreensaverController

            try:
                self._screensaver = ScreensaverController(manager, self._adapter, self._screensaver_after * 60, self)
            except NotImplementedError:
                logging.getLogger("canard.screensaver").error("no screensaver on this platform yet")
        if sys.platform == "darwin":
            from canard.app.saver_stream import SaverStreamServer

            self._stream = SaverStreamServer(self._manager, self._adapter, fps=self.saver_fps,
                                             enabled=self.saver_stream, off_hint=lambda: self.saver_off_hint,
                                             parent=self)

    def _on_interactive(self, enabled: bool) -> None:
        self.interactive = enabled
        self.changed.emit()
