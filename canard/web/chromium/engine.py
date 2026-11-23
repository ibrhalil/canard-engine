"""Chromium arka ucu (QtWebEngine): tüm platformlarda."""

import logging
import signal
from pathlib import Path

import shiboken6
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QImage
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from canard.app.settings import data_location
from canard.web import CHROMIUM, LABELS
from canard.web.base import WebEngine, WebHost
from canard.web.chromium import scripts
from canard.web.chromium import system_audio as system_audio_script
from canard.web.chromium.policy import decide as decide_permission

log = logging.getLogger("canard.web")

BRIDGE_SCRIPT = "canard-host-bridge"


def web_data_location() -> Path:
    """İçeriklerin localStorage, IndexedDB, çerez ve önbelleği (tüm kaynaklar tek profilde, origin'e göre ayrık)."""
    return data_location() / "webprofile"


def create_profile(persistent: bool = True) -> QWebEngineProfile:
    if persistent:
        profile = QWebEngineProfile("canard")
        profile.setPersistentStoragePath(str(web_data_location()))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    else:
        profile = QWebEngineProfile(None)
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    return profile


class ChromiumEngine(WebEngine):
    name = CHROMIUM
    label = LABELS[CHROMIUM]

    def __init__(self, persistent: bool = True):
        self.profile = create_profile(persistent)

    def version(self) -> str:
        from PySide6.QtWebEngineCore import qWebEngineChromiumVersion

        return f"Chromium {qWebEngineChromiumVersion()}"

    def data_location(self) -> Path:
        return web_data_location()

    def set_scripts(self, bridge: str, system_audio: bool) -> None:
        scripts.install(self.profile, BRIDGE_SCRIPT, bridge)
        if system_audio:
            system_audio_script.install(self.profile)
        else:
            system_audio_script.uninstall(self.profile)

    def create_host(self, boot_script: str | None = None, audio_hub=None) -> "ChromiumHost":
        return ChromiumHost(self.profile, boot_script=boot_script, audio_hub=audio_hub)

    def close(self) -> None:
        # Sayfalar profilden önce silinmeli; yoksa profil (localStorage, çerezler) diske düzgün yazılmayabilir.
        if self.profile is not None:
            shiboken6.delete(self.profile)
            self.profile = None


_CONSOLE_LEVELS = {
    QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel: logging.WARNING,
    QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel: logging.INFO,
    QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel: logging.DEBUG,
}


class CanardPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        # Canard'ın kendi boot/köprü mesajları ("[canard…") her zaman bilgi düzeyinde.
        log_level = _CONSOLE_LEVELS.get(level, logging.DEBUG)
        if message.startswith("[canard"):
            log_level = logging.INFO
        where = f"  ({source_id}:{line_number})" if source_id and log_level != logging.INFO else ""
        log.log(log_level, "console: %s%s", message, where)


class ChromiumHost(WebHost):
    refresh_after_occlusion = True  # kısa örtülmeden sonra ekran 30 sn+ donuk kalıyordu (PLAN.md, 2026-11-22)

    def __init__(self, profile: QWebEngineProfile, boot_script: str | None = None, audio_hub=None, parent=None):
        super().__init__(boot_script, parent)
        self.view = QWebEngineView()
        self.page = CanardPage(profile, self.view)
        self.view.setPage(self.page)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._set_view(self.view)
        self._quick = None

        if audio_hub is not None:
            from PySide6.QtWebChannel import QWebChannel

            page_audio = self.page_audio = audio_hub.page()
            self._channel = QWebChannel(self.page)
            self._channel.registerObject("canardAudio", self.page_audio)
            self.page.setWebChannel(self._channel)
            self.page.loadStarted.connect(page_audio.reset)
            self.destroyed.connect(lambda *_: audio_hub.forget(page_audio))

        self.page.loadFinished.connect(self._on_load_finished)
        self.page.permissionRequested.connect(decide_permission)
        self.page.desktopMediaRequested.connect(self._on_desktop_media_requested)
        try:
            self.view.renderProcessTerminated.connect(self._on_render_terminated)
        except AttributeError:
            pass

    def _on_desktop_media_requested(self, request) -> None:
        model = request.screensModel()
        if model.rowCount() > 0:
            log.debug("screen sharing requested: first screen selected")
            request.selectScreen(model.index(0, 0))
        else:
            log.warning("screen sharing requested but there is no screen to share")
            request.cancel()

    def _load(self, url: str) -> None:
        self.view.load(QUrl(url))

    def run_js(self, source: str) -> None:
        self.page.runJavaScript(source)

    def _load_blank(self) -> None:
        if self.suspended:  # Frozen sayfa gezinmez
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        self.page.setHtml("")

    def _apply_suspended(self, suspended: bool) -> None:
        # Görünmez: rAF durur; Frozen: JS görevleri de durur.
        log.debug("page %s", "frozen (hidden + Frozen)" if suspended else "active")
        if suspended:
            self.page.setVisible(False)
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
        else:
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            self.page.setVisible(True)

    def grab_frame(self, width: int, height: int, callback) -> None:
        # grabFramebuffer() GPU tamponunu doğrudan verir; QWidget.grab()'dan ~2 kat hızlı.
        if self._quick is None:
            self._quick = self.view.findChild(QQuickWidget)
        image = self._quick.grabFramebuffer() if self._quick is not None else self.view.grab().toImage()
        if image.isNull():
            callback(None)
            return
        if image.format() != QImage.Format.Format_ARGB32:
            image = image.convertToFormat(QImage.Format.Format_ARGB32)  # bellekte BGRA (CoreVideo RGBA desteklemiyor)
        if image.width() != width or image.height() != height:
            image = image.scaled(width, height)  # yalnız istisna (ör. etkileşimli boşluk)
        callback(image)

    def _on_load_finished(self, ok: bool) -> None:
        self._loaded(ok, self.page.url().toString())

    def _on_render_terminated(self, status=None, exit_code=None, *args) -> None:
        if getattr(status, "name", "") == "KilledTerminationStatus" and exit_code == signal.SIGTERM:
            # Canard kapanırken (ya da oturum kapanırken) süreçlere giden olağan sonlandırma; çökme değil.
            log.debug("page process stopped (SIGTERM): %s", self._current_url)
            return
        log.error("page process terminated: status=%s, exit code=%s (%s)",
                  getattr(status, "name", status), exit_code, self._current_url)
        self._show_error()
