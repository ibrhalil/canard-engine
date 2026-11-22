"""Menü çubuğu (`canard --tray`). Seçimler settings.json'da (`tray.*`, `saver.*`); hatalar iletişim kutusuyla gösterilir
(tepsi bildirimleri macOS'ta uygulama paketine bağlı)."""

import html
import logging
import platform
from pathlib import Path

from PySide6.QtCore import QKeyCombination, QObject, QPointF, QRectF, Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon

import canard
from canard.app import logs
from canard import i18n
from canard.app.application import LANGUAGE_KEY
from canard.content.sources import BUILD, DEVSERVER, REMOTE, SourceError, from_build, from_url, validate
from canard.i18n import N_, t
from canard.platform.base import detect
from canard.web.engine import web_data_location

log = logging.getLogger("canard.tray")

_DEFAULTS = {
    "tray.wallpaper": True,
    "tray.interactive": False,
    "tray.system_audio": True,
    "saver.enabled": True,
    "saver.fps": 30,
    "tray.disabled_screens": [],
}
_FPS_CHOICES = (24, 30)
_TYPE_LABELS = {BUILD: N_("local build"), REMOTE: N_("remote"), DEVSERVER: N_("dev server")}
_TOOLS = Path(canard.__file__).resolve().parents[1] / "tools"
_PRESET_LABELS = {"mscillus-boot-system.js": N_("mscillus: system audio"),
                  "mscillus-boot-mic.js": N_("mscillus: microphone")}
_ARROWS = {"←": Qt.Key.Key_Left, "→": Qt.Key.Key_Right}
_PERMISSIONS = {"system_audio": N_("System Audio Recording"), "microphone": N_("Microphone")}
_PERMISSION_STATES = {"granted": N_("allowed ✓"), "denied": N_("off ⚠︎"), "unknown": N_("not asked yet"),
                      None: N_("unknown")}
_REPO_URL = "https://github.com/ibrhalil/canard-engine"
_PRIVACY_URL = f"{_REPO_URL}/blob/main/docs/PERMISSIONS.md"


def runtime_options(store) -> dict:
    def pref(key):
        return store.get(key, _DEFAULTS[key])

    fps = pref("saver.fps")
    return {
        "wallpaper": bool(pref("tray.wallpaper")),
        "interactive": bool(pref("tray.interactive")),
        "system_audio": bool(pref("tray.system_audio")),
        "saver_stream": bool(pref("saver.enabled")),
        "saver_fps": fps if fps in _FPS_CHOICES else _DEFAULTS["saver.fps"],
        "disabled_screens": [name for name in pref("tray.disabled_screens") if isinstance(name, str)],
    }


def _boot_presets() -> list[tuple[str, str]]:
    """(etiket, betik) — depodaki hazır başlangıç betikleri (tools/*-boot-*.js)."""
    presets = []
    known = list(_PRESET_LABELS)
    for path in sorted(_TOOLS.glob("*-boot-*.js"), key=lambda p: (known.index(p.name) if p.name in known
                                                                  else len(known), p.name)):
        try:
            presets.append((t(_PRESET_LABELS.get(path.name, path.stem)), path.read_text(encoding="utf-8")))
        except OSError:
            continue
    return presets


def _icon() -> QIcon:
    """Tek renk şablon ikon (macOS açık/koyu menü çubuğuna uyarlar): çerçeve içinde dalga — tuval ve ses."""
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(18 * scale, 18 * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(Qt.GlobalColor.black, 1.5))
        painter.drawRoundedRect(QRectF(1.75, 3.25, 14.5, 11.5), 2.5, 2.5)
        wave = QPainterPath(QPointF(4.5, 9))
        wave.cubicTo(6, 4.5, 7.5, 4.5, 9, 9)
        wave.cubicTo(10.5, 13.5, 12, 13.5, 13.5, 9)
        painter.drawPath(wave)
        painter.end()
        icon.addPixmap(pixmap)
    icon.setIsMask(True)
    return icon


def _clear(menu: QMenu) -> None:
    # QMenu.clear() hemen siler; çağrı o menünün kendi eyleminden gelebileceği için silme ertelenir.
    for action in menu.actions():
        menu.removeAction(action)
        action.deleteLater()


def _type_label(kind: str) -> str:
    return t(_TYPE_LABELS[kind]) if kind in _TYPE_LABELS else kind


def _folder_link(path: Path) -> str:
    """Finder'da açılan klasör bağlantısı; dosya verilirse içinde bulunduğu klasör açılır, ev dizini ~ ile kısaltılır."""
    folder = path if path.is_dir() else path.parent
    home = str(Path.home())
    shown = "~" + str(path)[len(home):] if str(path).startswith(home) else str(path)
    href = bytes(QUrl.fromLocalFile(str(folder)).toEncoded()).decode()
    return f'<a href="{html.escape(href)}">{html.escape(shown)}</a>'


class TrayMenu(QObject):
    def __init__(self, app, runtime):
        super().__init__(runtime)
        self._app = app  # CanardApp
        self._runtime = runtime
        adapter = detect()
        try:
            self._platform = adapter.app_integration()
        except NotImplementedError:
            self._platform = None
        try:
            self._keys = adapter.hotkey_keys()
        except NotImplementedError:
            self._keys = {}
        self._permissions_shown: set[str] = set()
        self._menu = None
        self._tray = QSystemTrayIcon(_icon(), self)
        self._tray.setToolTip("Canard")
        self._build_menu()
        runtime.changed.connect(self._on_changed)
        runtime.error.connect(self.show_error)
        runtime.permission_needed.connect(self._explain_permission)
        application = QApplication.instance()
        application.screenAdded.connect(lambda *_: self._rebuild_screens())
        application.screenRemoved.connect(lambda *_: self._rebuild_screens())
        self._refresh()
        self._tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.error("menu bar icon cannot be shown (no system tray)")

    def _build_menu(self) -> None:
        """Menüyü seçili dilde kurar; dil değişince baştan kurulur."""
        runtime = self._runtime
        runtime.saver_off_hint = t("Canard menu › Screensaver › Stream to Canard.saver")
        old, menu = self._menu, QMenu()
        self._menu = menu
        self._status = menu.addAction("")
        self._status.setEnabled(False)
        menu.addSeparator()
        self._wallpaper = self._shortcut(self._check(menu, t("Wallpaper"), runtime.set_wallpaper), "toggle_wallpaper")
        self._pause = self._shortcut(self._check(menu, t("Pause"), runtime.set_paused), "toggle_pause")
        self._reload = self._shortcut(menu.addAction(t("Reload Page"), runtime.reload), "reload")
        self._interactive = self._shortcut(self._check(menu, t("Interactive Mode"), runtime.set_interactive),
                                           "toggle_interactive")
        self._screens = menu.addMenu(t("Screens"))
        self._sources = menu.addMenu(t("Source"))
        self._boot = menu.addMenu(t("Boot Script"))
        self._audio = self._check(menu, t("System Audio"), runtime.set_system_audio)
        permissions = menu.addMenu(t("Permissions"))
        self._permission_rows = {}
        for kind in _PERMISSIONS:
            self._permission_rows[kind] = permissions.addAction("")
            self._permission_rows[kind].setEnabled(False)
        permissions.addSeparator()
        for kind, title in _PERMISSIONS.items():
            permissions.addAction(t("Open {permission} Settings…", permission=t(title)),
                                  lambda k=kind: self._open_privacy(k))
        menu.addSeparator()
        self._start_saver = self._shortcut(menu.addAction("", runtime.start_screensaver), "screensaver_now")
        saver = menu.addMenu(t("Screensaver"))
        self._saver_state = saver.addAction("")
        self._saver_state.setEnabled(False)
        self._lock_state = saver.addAction("")
        self._lock_state.setEnabled(False)
        self._saver_install = saver.addAction("", self._install_saver)
        saver.addAction(t("Screensaver Settings…"), self._open_screensaver_settings)
        saver.addAction(t("Lock Screen Settings…"), self._open_lock_screen_settings)
        saver.addSeparator()
        self._saver_stream = self._check(saver, t("Stream to Canard.saver"), runtime.set_saver_stream)
        fps_menu = saver.addMenu(t("Frame Rate"))
        self._fps = {}
        for fps in _FPS_CHOICES:
            self._fps[fps] = self._check(fps_menu, t("{fps} fps", fps=fps),
                                         lambda _checked, f=fps: runtime.set_saver_fps(f))
        menu.addSeparator()
        self._login = self._check(menu, t("Open at Login"), self._set_login_item)
        self._languages = menu.addMenu(t("Language"))
        menu.addAction(t("Open Log Folder"), self._open_logs)
        menu.addAction(t("About Canard"), self._about)
        menu.addSeparator()
        menu.addAction(t("Quit Canard"), QApplication.quit)
        menu.aboutToShow.connect(self._refresh_system)
        self._tray.setContextMenu(menu)
        if old is not None:
            old.deleteLater()

    def close(self) -> None:
        self._tray.hide()

    def show_error(self, message: str) -> None:
        log.warning("%s", message)
        self._front()
        QMessageBox.warning(None, "Canard", message)

    def _shortcut(self, action, name: str):
        """Global kısayolu öğenin yanında gösterir; tuşu Carbon kısayolu yakalar, menü ikinci kez tetiklemez."""
        key = self._keys.get(name)
        if key:
            # macOS'ta Qt: Meta = ⌃, Alt = ⌥, Ctrl = ⌘.
            code = _ARROWS.get(key) or Qt.Key(ord(key))
            modifiers = (Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier
                         | Qt.KeyboardModifier.ControlModifier)
            action.setShortcut(QKeySequence(QKeyCombination(modifiers, code)))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            # Qt menü çubuğu menüsünü "context menu" sayar ve orada kısayolları gizler.
            action.setShortcutVisibleInContextMenu(True)
        return action

    def _check(self, menu: QMenu, text: str, handler):
        action = menu.addAction(text)
        action.setCheckable(True)
        action.triggered.connect(lambda checked: self._run(handler, checked))
        return action

    def _run(self, handler, *args) -> None:
        try:
            handler(*args)
        finally:
            self._refresh()  # reddedilen değişiklikte işaret geri alınır

    def _on_changed(self) -> None:
        runtime = self._runtime
        store = self._app.store
        store.set("tray.wallpaper", runtime.wallpaper)
        store.set("tray.interactive", runtime.interactive)
        store.set("tray.system_audio", runtime.system_audio)
        store.set("saver.enabled", runtime.saver_stream)
        store.set("saver.fps", runtime.saver_fps)
        store.set("tray.disabled_screens", sorted(runtime.disabled_screens))
        store.save()
        self._refresh()

    def _refresh(self) -> None:
        runtime = self._runtime
        source = runtime.source
        parts = [t("wallpaper on") if runtime.wallpaper else t("wallpaper off"),
                 f"{source.name} ({_type_label(source.type)})" if source else t("no source"),
                 t("stream on") if runtime.saver_stream else t("stream off")]
        if runtime.paused:
            parts.insert(1, t("⏸ paused"))
        if self._permission_problem():
            parts.append(t("⚠︎ permission needed"))
        self._status.setText("Canard — " + " · ".join(parts))
        self._wallpaper.setChecked(runtime.wallpaper)
        self._pause.setChecked(runtime.paused)
        self._pause.setEnabled(runtime.running and runtime.wallpaper)
        self._reload.setEnabled(runtime.running)
        self._interactive.setChecked(runtime.interactive and runtime.wallpaper)
        self._interactive.setEnabled(runtime.wallpaper and runtime.running)
        self._audio.setChecked(runtime.system_audio)
        self._saver_stream.setChecked(runtime.saver_stream)
        for fps, action in self._fps.items():
            action.setChecked(fps == runtime.saver_fps)
        self._rebuild_screens()
        self._rebuild_sources()
        self._rebuild_boot()
        self._rebuild_languages()
        self._refresh_system()

    def _refresh_system(self) -> None:
        # Menü dışında değişebilenler her açılışta yeniden okunur.
        platform = self._platform
        installed = platform is not None and platform.saver_installed()
        self._saver_state.setText(t("Canard.saver installed ✓") if installed else t("Canard.saver not installed"))
        self._saver_install.setText(t("Reinstall…") if installed else t("Install…"))
        self._saver_install.setEnabled(platform is not None and platform.saver_package() is not None)
        # Parola "hemen" değilse ekran koruyucuyu başlatmak oturumu kilitlemez.
        delay = platform.screen_lock_delay() if platform is not None else None
        if delay == 0:
            self._lock_state.setText(t("Password: immediately after the screensaver starts ✓"))
            self._start_saver.setText(t("Start Screensaver and Lock"))
        else:
            when = t("after {seconds} s", seconds=delay) if delay else t("not required")
            self._lock_state.setText(t("⚠︎ Password {when} — to lock, see Lock Screen Settings", when=when))
            self._start_saver.setText(t("Start Screensaver"))
        self._start_saver.setEnabled(platform is not None and self._runtime.running)
        for kind, title in _PERMISSIONS.items():
            state = _PERMISSION_STATES[self._runtime.permission_status(kind)]
            self._permission_rows[kind].setText(f"{t(title)}: {t(state)}")
        problem = t("⚠︎ permission needed")
        if self._permission_problem() and problem not in self._status.text():
            self._status.setText(f"{self._status.text()} · {problem}")
        login = platform is not None and platform.login_item_enabled()
        self._login.setChecked(login)
        self._login.setEnabled(login or (platform is not None and platform.app_bundle() is not None))

    def _rebuild_screens(self) -> None:
        # Kapatılıp şu an bağlı olmayan ekranlar da listede kalır ki yeniden açılabilsin.
        menu = self._screens
        _clear(menu)
        disabled = self._runtime.disabled_screens
        connected = [screen.name() for screen in QApplication.instance().screens()]
        primary = QApplication.instance().primaryScreen()
        for index, name in enumerate(connected + sorted(disabled - set(connected))):
            label = name or t("Screen {number}", number=index + 1)
            if primary is not None and name == primary.name():
                label += "  " + t("(main)")
            if name not in connected:
                label += "  " + t("(not connected)")
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(name not in disabled)
            action.triggered.connect(
                lambda checked, n=name: self._run(self._runtime.set_screen_enabled, n, checked))

    def _rebuild_sources(self) -> None:
        menu = self._sources
        _clear(menu)
        active = self._runtime.source
        sources = self._app.registry.list()
        for source in sources:
            action = menu.addAction(f"{source.name} ({_type_label(source.type)})")
            action.setCheckable(True)
            action.setChecked(active is not None and source.id == active.id)
            action.setToolTip(source.url or source.path or "")
            action.triggered.connect(lambda _checked, s=source: self._run(self._open_source, s))
        if sources:
            menu.addSeparator()
        menu.addAction(t("Add URL…"), self._add_url)
        menu.addAction(t("Add Folder…"), self._add_folder)
        if sources:
            self._shortcut(menu.addAction(t("Next Source"), self._runtime.next_source), "next_source").setEnabled(
                len(sources) > 1)
            remove = menu.addMenu(t("Remove Source"))
            for source in sources:
                remove.addAction(source.name).triggered.connect(
                    lambda _checked, s=source: self._run(self._remove_source, s))

    def _rebuild_boot(self) -> None:
        menu = self._boot
        _clear(menu)
        source = self._runtime.source
        menu.menuAction().setEnabled(source is not None)
        current = source.boot_script if source else None
        options = [(t("None"), None), *_boot_presets()]
        for label, text in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == text)
            action.triggered.connect(lambda _checked, t=text: self._run(self._set_boot_script, t))
        if current and all(current != text for _label, text in options):
            custom = menu.addAction(t("Script from file"))
            custom.setCheckable(True)
            custom.setChecked(True)
            custom.setEnabled(False)
        menu.addSeparator()
        menu.addAction(t("Choose File…"), self._choose_boot_file)

    def _open_source(self, source) -> None:
        if self._runtime.source is not None and self._runtime.source.id == source.id:
            return
        try:
            self._runtime.set_source(source)
        except SourceError as error:
            self.show_error(t("Could not open source: {error}", error=error))

    def _add_source(self, source, existing) -> None:
        if existing is None:
            errors = validate(source)
            if errors:
                self.show_error("\n".join(errors))
                return
            existing = self._app.registry.add(source)
            log.info("source added: %s (%s)", existing.id, existing.name)
        self._run(self._open_source, existing)

    def _add_url(self) -> None:
        self._front()
        url, ok = QInputDialog.getText(None, t("Canard — Add URL"), t("Remote site or dev server address (http/https):"))
        url = url.strip()
        if ok and url:
            self._add_source(from_url(url), self._app.registry.find(url=url))

    def _add_folder(self) -> None:
        self._front()
        path = QFileDialog.getExistingDirectory(None, t("Canard — Choose a build folder (containing index.html)"),
                                                str(Path.home()))
        if path:
            source = from_build(path)
            self._add_source(source, self._app.registry.find(path=source.path))

    def _remove_source(self, source) -> None:
        self._front()
        answer = QMessageBox.question(
            None, t("Canard — Remove Source"),
            t("Remove \"{name}\" from the list?\n{location}\n\n"
              "The content's local data (localStorage, cookies) is not deleted.",
              name=source.name, location=source.url or source.path))
        if answer != QMessageBox.StandardButton.Yes:
            return
        active = self._runtime.source
        if active is not None and active.id == source.id:
            if len(self._app.registry.list()) < 2:
                self.show_error(t("The only source cannot be removed; add another source first."))
                return
            self._runtime.next_source()
            if self._runtime.source is None or self._runtime.source.id == source.id:
                return  # geçilemedi, hata gösterildi
        self._app.registry.remove(source.id)
        log.info("source removed: %s (%s)", source.id, source.name)

    def _set_boot_script(self, text: str | None) -> None:
        source = self._runtime.source
        if source is not None and source.boot_script != text:
            self._runtime.set_boot_script(text)

    def _choose_boot_file(self) -> None:
        self._front()
        start = _TOOLS if _TOOLS.is_dir() else Path.home()
        path, _filter = QFileDialog.getOpenFileName(None, t("Canard — Choose a boot script"), str(start),
                                                    "JavaScript (*.js)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            self.show_error(t("Could not read the script: {error}", error=error))
            return
        self._run(self._set_boot_script, text)

    def _set_login_item(self, enabled: bool) -> None:
        try:
            self._platform.set_login_item(enabled)
        except OSError as error:
            self.show_error(t("Could not change Open at Login: {error}", error=error))
        else:
            log.info("open at login %s", "on" if enabled else "off")

    def _install_saver(self) -> None:
        try:
            self._platform.install_saver()
        except Exception as error:
            self.show_error(t("Could not install Canard.saver: {error}", error=error))
        else:
            log.info("Canard.saver installed (screensaver process restarted)")

    def _open_screensaver_settings(self) -> None:
        if self._platform is not None:
            self._platform.open_screensaver_settings()

    def _permission_problem(self) -> bool:
        # Yalnız kullanılan izinler sayılır.
        runtime = self._runtime
        return ((runtime.system_audio and runtime.permission_status("system_audio") == "denied")
                or (runtime.mic_requested and runtime.permission_status("microphone") == "denied"))

    def _explain_permission(self, kind: str) -> None:
        """İçerik sesi istedi ama macOS izni kapalı: oturum başına bir kez açıklayıp ayarlara yönlendirir."""
        self._refresh()
        if kind in self._permissions_shown:
            return
        self._permissions_shown.add(kind)
        if kind == "system_audio":
            title = t("Canard can't hear system audio")
            text = t("The \"Screen & System Audio Recording\" permission is off for Canard, so the visualizer won't "
                     "react to music.\n\nTurn on Canard in System Settings › Privacy & Security › Screen & System "
                     "Audio Recording, under \"System Audio Recording Only\". The change applies after Canard restarts.")
        else:
            title = t("Canard can't use the microphone")
            text = t("The content asked for the microphone, but the microphone permission is off for Canard.\n\n"
                     "Turn on Canard in System Settings › Privacy & Security › Microphone. The change applies after "
                     "Canard restarts.")
        self._front()
        box = QMessageBox(QMessageBox.Icon.Warning, title, text)
        settings = box.addButton(t("Open Settings"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(t("Later"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is settings:
            self._open_privacy(kind)

    def _open_privacy(self, kind: str) -> None:
        if self._platform is not None:
            self._platform.open_privacy_settings(kind)

    def _open_lock_screen_settings(self) -> None:
        if self._platform is not None:
            self._platform.open_lock_screen_settings()

    def _open_logs(self) -> None:
        folder = logs.default_log_path().parent
        if folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        else:
            self.show_error(t("The log folder doesn't exist yet: {path}", path=folder))

    def _about(self) -> None:
        self._front()
        try:
            from PySide6.QtWebEngineCore import qWebEngineChromiumVersion

            chromium = f" · Chromium {qWebEngineChromiumVersion()}"
        except ImportError:
            chromium = ""
        folders = (
            (t("Settings"), "", Path(self._app.settings_path)),
            (t("Content data"), " " + t("(localStorage, IndexedDB, cookies)"), web_data_location()),
            (t("Logs"), "", logs.default_log_path().parent),
        )
        rows = "".join(f"<b>{title}</b>{detail}<br>{_folder_link(path)}<br>" for title, detail, path in folders)
        box = QMessageBox()
        box.setWindowTitle(t("About Canard"))
        box.setIconPixmap(QApplication.windowIcon().pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.setText(f"<b>Canard {canard.__version__}</b>")
        box.setInformativeText(
            t("Runs web content as a live wallpaper and screensaver.") + "<br><br>"
            f'<a href="{_REPO_URL}">{_REPO_URL.removeprefix("https://")}</a> · {t("License")}: GNU GPL v3<br>'
            f"<small>Python {platform.python_version()} · Qt {qVersion()}{chromium}</small><br><br>"
            + t("Canard collects no data; everything stays on this computer.")
            + f' <a href="{_PRIVACY_URL}">{t("Details")}</a><br><br>'
            f"{rows}<br><small>"
            + t("All sources share one web profile; data is kept separate per site address (origin). "
                "Removing a source from the list does not delete its data.")
            + "</small>")
        box.exec()

    def _rebuild_languages(self) -> None:
        menu = self._languages
        _clear(menu)
        choice = self._app.store.get(LANGUAGE_KEY, i18n.SYSTEM)
        options = [(i18n.SYSTEM, t("System Default")), *i18n.LANGUAGES.items()]
        for code, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(code == choice or (code == i18n.SYSTEM and choice not in i18n.LANGUAGES))
            action.triggered.connect(lambda _checked, c=code: self._run(self._set_language, c))

    def _set_language(self, choice: str) -> None:
        store = self._app.store
        store.set(LANGUAGE_KEY, choice)
        store.save()
        code = i18n.resolve(choice)
        log.info("language: %s (%s)", code, choice)
        if code == i18n.language:
            return
        i18n.set_language(code)
        self._app.apply_qt_language()
        self._build_menu()

    def _front(self) -> None:
        if self._platform is not None:
            self._platform.bring_to_front()

