"""Çalışma zamanı: duvar kağıdı + Canard.saver yayını ve çalışırken değişebilen ayarlar.

CLI (`--mode wallpaper|screensaver`) ve menü çubuğu (`--tray`) aynı nesneyi kullanır. Kaynak, boot script, duvar
kağıdı, etkileşimli mod, sistem sesi ve yayın uygulama kapanmadan değiştirilebilir; sayfa yalnız gerektiğinde
(kaynak, boot script, sistem sesi) yeniden yüklenir.
"""

import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal

from canard.app.application import OCCLUSION_GRACE_MS, WallpaperManager
from canard.app.saver_stream import DEFAULT_FPS
from canard.audio.hub import SystemAudioHub
from canard.content.sources import SourceError
from canard.platform.base import detect
from canard.web import system_audio as system_audio_script
from canard.web.bridge import HostBridge

log = logging.getLogger("canard.runtime")


class Runtime(QObject):
    changed = Signal()
    error = Signal(str)  # kısayolla tetiklenen eylem başarısız olunca (menü çubuğu iletişim kutusuyla gösterir)
    permission_needed = Signal(str)  # "system_audio" | "microphone": içerik istedi ama macOS izni kapalı

    def __init__(self, app, profile, *, wallpaper: bool = True, interactive: bool = False,
                 system_audio: bool = True, saver_stream: bool = True, saver_fps: int | None = None,
                 screensaver_after: float | None = None, disabled_screens=(), parent=None):
        super().__init__(parent)
        self._app = app  # CanardApp: kaynak kaydı ve yerel sunucular
        self._profile = profile
        self._adapter = detect()
        self.wallpaper = wallpaper
        self.interactive = interactive
        self.system_audio = system_audio
        self.saver_stream = saver_stream and sys.platform == "darwin"
        self.saver_fps = saver_fps or DEFAULT_FPS
        self._screensaver_after = screensaver_after
        # Yayın kapalıyken Canard.saver'da görünen ipucu (menü çubuğu kendi yolunu yazar).
        self.saver_off_hint = "Açmak için Canard'ı --no-saver-stream olmadan başlat"
        self.disabled_screens: set[str] = set(disabled_screens)
        self.source = None
        self.url = None
        # Hub her sayfaya bağlanır; yakalama yalnız bir sayfa ses isteyince başlar. Sistem sesi kapalıyken
        # sayfaya köprü betiği eklenmez, içerik hub'a ulaşamaz.
        self._audio_hub = SystemAudioHub(self._adapter, self)
        self._audio_hub.started.connect(lambda: self._check_permission("system_audio"))
        self.mic_requested = False
        from canard.web import policy

        policy.media_listeners.append(self._on_media_request)
        self._manager = None
        self._stream = None
        self._screensaver = None
        self._install_scripts()

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
        log.info("kaynak: %s (%s) · %s%s", source.name, source.type, url,
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
        log.info("boot script %s", "bağlandı" if boot_script else "kaldırıldı")
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
        log.info("ekran %s: %s", "açık" if enabled else "kapalı", name)
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
            log.info("sayfa yeniden yükleniyor")
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
            log.warning("kaynak açılamadı: %s", error)
            self.error.emit(f"Kaynak açılamadı ({target.name}): {error}")

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
        log.info("sistem sesi köprüsü %s", "açık" if enabled else "kapalı")
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
        log.info("ekran koruyucu kare hızı: %d (yeni bağlantılarda)", fps)
        self.changed.emit()

    def start_screensaver(self) -> None:
        """⌃⌥⌘L / menü: --screensaver-after verildiyse Canard'ın kendi boşta tam ekranı, yoksa sistem ekran koruyucusu."""
        if self._screensaver is not None:
            self._screensaver.start_now()
            return
        try:
            self._adapter.app_integration().start_screensaver()
        except NotImplementedError:
            log.warning("bu platformda ekran koruyucu başlatılamıyor")
            return
        log.info("ekran koruyucu başlatıldı")

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
            log.warning("%s izni kapalı: içerik bu sesi alamaz", "sistem sesi kaydı" if kind == "system_audio"
                        else "mikrofon")
            self.permission_needed.emit(kind)
        elif status == "unknown" and retry:
            # İlk kullanımda macOS kendi izin penceresini açar; kullanıcı yanıtladıktan sonra yeniden bakılır.
            QTimer.singleShot(8000, lambda: self._check_permission(kind, retry=False))

    def close(self) -> None:
        from canard.web import policy

        if self._on_media_request in policy.media_listeners:
            policy.media_listeners.remove(self._on_media_request)
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

    def _install_scripts(self) -> None:
        mode = "wallpaper" if self.wallpaper else "screensaver"
        source = self.source.to_dict() if self.source else None
        HostBridge(mode=mode, source=source, system_audio=self.system_audio).install(self._profile)
        if self.system_audio:
            system_audio_script.install(self._profile)
        else:
            system_audio_script.uninstall(self._profile)

    def _start(self) -> None:
        manager = self._manager = WallpaperManager(
            self._profile, self.url, boot_script=self.source.boot_script, interactive=self.interactive,
            audio_hub=self._audio_hub, show_wallpaper=self.wallpaper, parent=self)
        manager.set_disabled_screens(self.disabled_screens)
        manager.interactiveChanged.connect(self._on_interactive)
        manager.pausedChanged.connect(lambda _paused: self.changed.emit())
        manager.occlusion_grace_ms = OCCLUSION_GRACE_MS if self.saver_stream else 0
        # Kısayol, denetleyici oluşmadan kaydedilir; çağrıldığında denetleyiciye yönlenir.
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
                logging.getLogger("canard.screensaver").error("bu platformda ekran koruyucu henüz yok")
        if sys.platform == "darwin":
            from canard.app.saver_stream import SaverStreamServer

            self._stream = SaverStreamServer(self._manager, self._adapter, fps=self.saver_fps,
                                             enabled=self.saver_stream, off_hint=self.saver_off_hint, parent=self)

    def _on_interactive(self, enabled: bool) -> None:
        self.interactive = enabled
        self.changed.emit()
