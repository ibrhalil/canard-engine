import json
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QLabel, QPushButton, QStackedLayout, QVBoxLayout, QWidget
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from canard.web.bridge import HostBridge
from canard.web.policy import decide as decide_permission


def data_location() -> Path:
    from PySide6.QtCore import QStandardPaths

    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def create_profile(persistent: bool = True) -> QWebEngineProfile:
    if persistent:
        profile = QWebEngineProfile("canard")
        profile.setPersistentStoragePath(str(data_location() / "webprofile"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
    else:
        profile = QWebEngineProfile(None)
    settings = profile.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
    return profile


class CanardPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        print(f"[web:{level}] {message}  ({source_id}:{line_number})", file=sys.stderr, flush=True)


class WebHost(QWidget):
    def __init__(self, profile: QWebEngineProfile, boot_script: str | None = None, audio_hub=None, parent=None):
        super().__init__(parent)
        self._current_url: str | None = None
        self._boot_script = boot_script
        self._suspended = False
        self._audio_hub = audio_hub
        self.page_audio = None

        self.view = QWebEngineView()
        self.page = CanardPage(profile, self.view)
        self.view.setPage(self.page)
        self.view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        retry_button = QPushButton("Yeniden Dene")
        retry_button.clicked.connect(self.retry)
        overlay = QWidget()
        layout = QVBoxLayout(overlay)
        layout.addStretch(1)
        layout.addWidget(self._error_label)
        layout.addWidget(retry_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        self._stack = QStackedLayout(self)
        self._stack.addWidget(self.view)
        self._stack.addWidget(overlay)

        if audio_hub is not None:
            from PySide6.QtWebChannel import QWebChannel

            page_audio = self.page_audio = audio_hub.page()
            self._channel = QWebChannel(self.page)
            self._channel.registerObject("canardAudio", self.page_audio)
            self.page.setWebChannel(self._channel)
            self.page.loadStarted.connect(page_audio.reset)
            self.destroyed.connect(lambda *_: audio_hub.forget(page_audio))

        self.page.loadFinished.connect(self._on_load_finished)
        self.page.permissionRequested.connect(self._on_permission_requested)
        self.page.desktopMediaRequested.connect(self._on_desktop_media_requested)
        try:
            self.view.renderProcessTerminated.connect(self._on_render_terminated)
        except AttributeError:
            pass

    def _on_permission_requested(self, permission) -> None:
        decide_permission(permission)

    def _on_desktop_media_requested(self, request) -> None:
        model = request.screensModel()
        if model.rowCount() > 0:
            request.selectScreen(model.index(0, 0))
        else:
            request.cancel()

    def install_bridge(self, bridge: HostBridge) -> None:
        bridge.install(self.page.profile())

    def load(self, url: str) -> None:
        self._current_url = url
        self._stack.setCurrentIndex(0)
        self.view.load(QUrl(url))

    def retry(self) -> None:
        if self._current_url:
            self.load(self._current_url)

    def send_key(self, key: str) -> None:
        # Sentetik keydown/keyup: document'a gönderilir, window dinleyicilerine de kabarcıklanır.
        self.page.runJavaScript(
            "(function(k){var o={key:k,code:k,bubbles:true,cancelable:true};"
            "document.dispatchEvent(new KeyboardEvent('keydown',o));"
            "document.dispatchEvent(new KeyboardEvent('keyup',o));})(%s);" % json.dumps(key)
        )

    def set_suspended(self, suspended: bool) -> None:
        # Görünmez sayfa: visibilityState=hidden, rAF durur; ardından Frozen ile JS görevleri de durur.
        if suspended == self._suspended:
            return
        self._suspended = suspended
        if self.page_audio is not None:
            self.page_audio.set_paused(suspended)
        if suspended:
            self.page.setVisible(False)
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Frozen)
        else:
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
            self.page.setVisible(True)

    def _on_load_finished(self, ok: bool) -> None:
        if ok:
            self._stack.setCurrentIndex(0)
            if self._boot_script:
                self.page.runJavaScript(self._boot_script)
        else:
            self._show_error()

    def _on_render_terminated(self, *args) -> None:
        self._show_error()

    def _show_error(self) -> None:
        self._error_label.setText(f"İçerik yüklenemedi:\n{self._current_url or ''}")
        self._stack.setCurrentIndex(1)
