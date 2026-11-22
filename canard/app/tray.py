"""Menü çubuğu (`canard --tray`): duvar kağıdı, kaynak, başlangıç betiği, sistem sesi, ekran koruyucu, oturum açılışı.

Seçimler settings.json'da kalır (`tray.*`, `saver.*`); kaynaklar ve başlangıç betiği zaten kaynak kaydında.
Hatalar iletişim kutusuyla gösterilir: tepsi bildirimleri (showMessage) macOS'ta uygulama paketine bağlı.
"""

import logging
from pathlib import Path

from PySide6.QtCore import QKeyCombination, QObject, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon

import canard
from canard.app import logs
from canard.content.sources import BUILD, DEVSERVER, REMOTE, SourceError, from_build, from_url, validate
from canard.platform.base import detect

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
_TYPE_LABELS = {BUILD: "yerel build", REMOTE: "uzak", DEVSERVER: "dev sunucu"}
_TOOLS = Path(canard.__file__).resolve().parents[1] / "tools"
_PRESET_LABELS = {"mscillus-boot-system.js": "mscillus: sistem sesi", "mscillus-boot-mic.js": "mscillus: mikrofon"}
_ARROWS = {"←": Qt.Key.Key_Left, "→": Qt.Key.Key_Right}
_PERMISSIONS = {"system_audio": "Sistem sesi kaydı", "microphone": "Mikrofon"}
_PERMISSION_STATES = {"granted": "izin verildi ✓", "denied": "kapalı ⚠︎", "unknown": "henüz sorulmadı",
                      None: "bilinmiyor"}


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
            presets.append((_PRESET_LABELS.get(path.name, path.stem), path.read_text(encoding="utf-8")))
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
    # QMenu.clear() eylemleri hemen siler; menüyü yeniden kuran çağrı o menünün bir eyleminin sinyalinden
    # (ya da onun açtığı iletişim kutusundan) gelebilir → silme olay döngüsüne bırakılır.
    for action in menu.actions():
        menu.removeAction(action)
        action.deleteLater()


class TrayMenu(QObject):
    def __init__(self, app, runtime):
        super().__init__(runtime)
        self._app = app  # CanardApp: kaynak kaydı ve ayarlar
        self._runtime = runtime
        runtime.saver_off_hint = "Canard menüsü › Ekran koruyucu › Canard.saver'a yayın"
        adapter = detect()
        try:
            self._platform = adapter.app_integration()
        except NotImplementedError:
            self._platform = None
        try:
            self._keys = adapter.hotkey_keys()
        except NotImplementedError:
            self._keys = {}

        menu = self._menu = QMenu()
        self._status = menu.addAction("")
        self._status.setEnabled(False)
        menu.addSeparator()
        # İçerik
        self._wallpaper = self._shortcut(self._check(menu, "Duvar kağıdı", runtime.set_wallpaper), "toggle_wallpaper")
        self._pause = self._shortcut(self._check(menu, "Duraklat", runtime.set_paused), "toggle_pause")
        self._reload = self._shortcut(menu.addAction("Sayfayı yeniden yükle", runtime.reload), "reload")
        self._interactive = self._shortcut(self._check(menu, "Etkileşimli mod", runtime.set_interactive),
                                           "toggle_interactive")
        self._screens = menu.addMenu("Ekranlar")
        self._sources = menu.addMenu("Kaynak")
        self._boot = menu.addMenu("Başlangıç betiği")
        self._audio = self._check(menu, "Sistem sesi", runtime.set_system_audio)
        permissions = menu.addMenu("İzinler")
        self._permission_rows = {}
        for kind, title in _PERMISSIONS.items():
            self._permission_rows[kind] = permissions.addAction(title)
            self._permission_rows[kind].setEnabled(False)
        permissions.addSeparator()
        for kind, title in _PERMISSIONS.items():
            permissions.addAction(f"{title} izin ayarlarını aç…", lambda k=kind: self._open_privacy(k))
        self._permissions_shown: set[str] = set()
        menu.addSeparator()
        # Ekran koruyucu
        self._start_saver = self._shortcut(menu.addAction("Ekran koruyucuyu başlat", runtime.start_screensaver),
                                           "screensaver_now")
        saver = menu.addMenu("Ekran koruyucu")
        self._saver_state = saver.addAction("")
        self._saver_state.setEnabled(False)
        self._lock_state = saver.addAction("")
        self._lock_state.setEnabled(False)
        self._saver_install = saver.addAction("Kur…", self._install_saver)
        saver.addAction("Ekran koruyucu ayarları…", self._open_screensaver_settings)
        saver.addAction("Kilit ekranı ayarları…", self._open_lock_screen_settings)
        saver.addSeparator()
        self._saver_stream = self._check(saver, "Canard.saver'a yayın", runtime.set_saver_stream)
        fps_menu = saver.addMenu("Kare hızı")
        self._fps = {}
        for fps in _FPS_CHOICES:
            self._fps[fps] = self._check(fps_menu, f"{fps} kare/sn", lambda _checked, f=fps: runtime.set_saver_fps(f))
        menu.addSeparator()
        # Uygulama
        self._login = self._check(menu, "Oturum açılışında başlat", self._set_login_item)
        menu.addAction("Log klasörünü aç", self._open_logs)
        menu.addAction("Hakkında", self._about)
        menu.addSeparator()
        menu.addAction("Çık", QApplication.quit)
        menu.aboutToShow.connect(self._refresh_system)

        self._tray = QSystemTrayIcon(_icon(), self)
        self._tray.setToolTip("Canard")
        self._tray.setContextMenu(menu)
        runtime.changed.connect(self._on_changed)
        runtime.error.connect(self.show_error)
        runtime.permission_needed.connect(self._explain_permission)
        application = QApplication.instance()
        application.screenAdded.connect(lambda *_: self._rebuild_screens())
        application.screenRemoved.connect(lambda *_: self._rebuild_screens())
        self._refresh()
        self._tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.error("menü çubuğu simgesi gösterilemiyor (sistem tepsisi yok)")

    def close(self) -> None:
        self._tray.hide()

    def show_error(self, message: str) -> None:
        log.warning("%s", message)
        self._front()
        QMessageBox.warning(None, "Canard", message)

    # Durum

    def _shortcut(self, action, name: str):
        """Global kısayolu öğenin yanında, macOS'un sağa dayalı kısayol sütununda gösterir. Tuşa basınca eylemi global
        kısayol (Carbon) çalıştırır; kayıtlı kısayolu sistem yakaladığı için menü onu ikinci kez tetiklemez. Qt kısayolu
        yalnız bu öğeye bağlı kalsın diye en dar bağlam seçilir."""
        key = self._keys.get(name)
        if key:
            # macOS'ta Qt: Meta = ⌃, Alt = ⌥, Ctrl = ⌘.
            code = _ARROWS.get(key) or Qt.Key(ord(key))
            modifiers = (Qt.KeyboardModifier.MetaModifier | Qt.KeyboardModifier.AltModifier
                         | Qt.KeyboardModifier.ControlModifier)
            action.setShortcut(QKeySequence(QKeyCombination(modifiers, code)))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            # Menü çubuğu menüsü Qt'de "context menu" sayılır ve macOS'ta Qt orada kısayolları varsayılan olarak gizler
            # (AA_DontShowShortcutsInContextMenus = True); öğe başına açıkça gösterilir.
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
            self._refresh()  # reddedilen değişiklikte (ör. duvar kağıdı kapalıyken etkileşimli) işaret geri alınır

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
        parts = ["duvar kağıdı açık" if runtime.wallpaper else "duvar kağıdı kapalı",
                 f"{source.name} ({_TYPE_LABELS.get(source.type, source.type)})" if source else "kaynak yok",
                 "yayın açık" if runtime.saver_stream else "yayın kapalı"]
        if runtime.paused:
            parts.insert(1, "⏸ duraklatıldı")
        if self._permission_problem():
            parts.append("⚠︎ izin gerekli")
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
        self._refresh_system()

    def _refresh_system(self) -> None:
        # Menü dışında değişebilenler (Canard.saver kurulumu, giriş öğesi) her açılışta yeniden okunur.
        platform = self._platform
        installed = platform is not None and platform.saver_installed()
        self._saver_state.setText("Canard.saver kurulu ✓" if installed else "Canard.saver kurulu değil")
        self._saver_install.setText("Yeniden kur…" if installed else "Kur…")
        self._saver_install.setEnabled(platform is not None and platform.saver_package() is not None)
        # Kilit ekranının üstünde yalnız ekran koruyucu çalışır; parola "hemen" değilse başlatmak oturumu kilitlemez.
        delay = platform.screen_lock_delay() if platform is not None else None
        if delay == 0:
            self._lock_state.setText("Parola: ekran koruyucu başlayınca hemen ✓")
            self._start_saver.setText("Ekran koruyucuyu başlat ve kilitle")
        else:
            when = f"{delay} sn sonra" if delay else "istenmiyor"
            self._lock_state.setText(f"⚠︎ Parola {when} — kilit için Kilit ekranı ayarları")
            self._start_saver.setText("Ekran koruyucuyu başlat")
        self._start_saver.setEnabled(platform is not None and self._runtime.running)
        for kind, title in _PERMISSIONS.items():
            self._permission_rows[kind].setText(f"{title}: {_PERMISSION_STATES[self._runtime.permission_status(kind)]}")
        if self._permission_problem() and "⚠︎ izin gerekli" not in self._status.text():
            self._status.setText(self._status.text() + " · ⚠︎ izin gerekli")
        login = platform is not None and platform.login_item_enabled()
        self._login.setChecked(login)
        self._login.setEnabled(login or (platform is not None and platform.app_bundle() is not None))

    def _rebuild_screens(self) -> None:
        # Bağlı ekranlar; kapatılıp şu an bağlı olmayanlar da listede kalır ki yeniden açılabilsin.
        menu = self._screens
        _clear(menu)
        disabled = self._runtime.disabled_screens
        connected = [screen.name() for screen in QApplication.instance().screens()]
        primary = QApplication.instance().primaryScreen()
        for index, name in enumerate(connected + sorted(disabled - set(connected))):
            label = (name or f"Ekran {index + 1}") + ("  (ana ekran)" if primary is not None and name == primary.name() else "")
            if name not in connected:
                label += "  (bağlı değil)"
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
            action = menu.addAction(f"{source.name} ({_TYPE_LABELS.get(source.type, source.type)})")
            action.setCheckable(True)
            action.setChecked(active is not None and source.id == active.id)
            action.setToolTip(source.url or source.path or "")
            action.triggered.connect(lambda _checked, s=source: self._run(self._open_source, s))
        if sources:
            menu.addSeparator()
        menu.addAction("URL ekle…", self._add_url)
        menu.addAction("Klasör ekle…", self._add_folder)
        if sources:
            self._shortcut(menu.addAction("Sonraki kaynak", self._runtime.next_source), "next_source").setEnabled(
                len(sources) > 1)
            remove = menu.addMenu("Kaynağı kaldır")
            for source in sources:
                remove.addAction(source.name).triggered.connect(
                    lambda _checked, s=source: self._run(self._remove_source, s))

    def _rebuild_boot(self) -> None:
        menu = self._boot
        _clear(menu)
        source = self._runtime.source
        menu.menuAction().setEnabled(source is not None)  # Qt yerel menüde alt menüyü açan öğe kapatılır
        current = source.boot_script if source else None
        options = [("Yok", None), *_boot_presets()]
        for label, text in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(current == text)
            action.triggered.connect(lambda _checked, t=text: self._run(self._set_boot_script, t))
        if current and all(current != text for _label, text in options):
            custom = menu.addAction("Dosyadan seçilen betik")
            custom.setCheckable(True)
            custom.setChecked(True)
            custom.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Dosya seç…", self._choose_boot_file)

    # Eylemler

    def _open_source(self, source) -> None:
        if self._runtime.source is not None and self._runtime.source.id == source.id:
            return
        try:
            self._runtime.set_source(source)
        except SourceError as error:
            self.show_error(f"Kaynak açılamadı: {error}")

    def _add_source(self, source, existing) -> None:
        if existing is None:
            errors = validate(source)
            if errors:
                self.show_error("\n".join(errors))
                return
            existing = self._app.registry.add(source)
            log.info("kaynak eklendi: %s (%s)", existing.id, existing.name)
        self._run(self._open_source, existing)

    def _add_url(self) -> None:
        self._front()
        url, ok = QInputDialog.getText(None, "Canard — URL ekle", "Uzak site ya da dev sunucusu adresi (http/https):")
        url = url.strip()
        if ok and url:
            self._add_source(from_url(url), self._app.registry.find(url=url))

    def _add_folder(self) -> None:
        self._front()
        path = QFileDialog.getExistingDirectory(None, "Canard — build klasörü seç (index.html içeren)",
                                                str(Path.home()))
        if path:
            source = from_build(path)
            self._add_source(source, self._app.registry.find(path=source.path))

    def _remove_source(self, source) -> None:
        self._front()
        answer = QMessageBox.question(
            None, "Canard — kaynağı kaldır",
            f"\"{source.name}\" kaynağı listeden kaldırılsın mı?\n{source.url or source.path}\n\n"
            "İçeriğin yerel verileri (localStorage, çerezler) silinmez.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        active = self._runtime.source
        if active is not None and active.id == source.id:
            if len(self._app.registry.list()) < 2:
                self.show_error("Gösterilen tek kaynak kaldırılamaz; önce başka bir kaynak ekle.")
                return
            self._runtime.next_source()  # önce başka kaynağa geçilir
            if self._runtime.source is None or self._runtime.source.id == source.id:
                return  # geçilemedi (hata zaten gösterildi)
        self._app.registry.remove(source.id)
        log.info("kaynak kaldırıldı: %s (%s)", source.id, source.name)

    def _set_boot_script(self, text: str | None) -> None:
        source = self._runtime.source
        if source is not None and source.boot_script != text:
            self._runtime.set_boot_script(text)

    def _choose_boot_file(self) -> None:
        self._front()
        start = _TOOLS if _TOOLS.is_dir() else Path.home()
        path, _filter = QFileDialog.getOpenFileName(None, "Canard — başlangıç betiği seç", str(start),
                                                    "JavaScript (*.js)")
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            self.show_error(f"Betik okunamadı: {error}")
            return
        self._run(self._set_boot_script, text)

    def _set_login_item(self, enabled: bool) -> None:
        try:
            self._platform.set_login_item(enabled)
        except OSError as error:
            self.show_error(f"Oturum açılışı ayarlanamadı: {error}")
        else:
            log.info("oturum açılışında başlatma %s", "açık" if enabled else "kapalı")

    def _install_saver(self) -> None:
        try:
            self._platform.install_saver()
        except Exception as error:
            self.show_error(f"Canard.saver kurulamadı: {error}")
        else:
            log.info("Canard.saver kuruldu (ekran koruyucu süreci yeniden başlatıldı)")

    def _open_screensaver_settings(self) -> None:
        if self._platform is not None:
            self._platform.open_screensaver_settings()

    def _permission_problem(self) -> bool:
        # Yalnız kullanılan izinler: sistem sesi köprüsü açıksa sistem sesi, sayfa mikrofon istediyse mikrofon.
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
            title = "Canard sistem sesini duyamıyor"
            text = ("macOS'ta Canard için \"Ekran ve Sistem Sesi Kaydı\" izni kapalı; görselleştirici çalan müziğe "
                    "tepki vermez.\n\nSistem Ayarları › Gizlilik ve Güvenlik › Ekran ve Sistem Sesi Kaydı'nda "
                    "\"Yalnızca Sistem Sesi Kaydı\" altında Canard'ı aç. Değişiklik Canard yeniden açılınca geçerli olur.")
        else:
            title = "Canard mikrofonu kullanamıyor"
            text = ("İçerik mikrofon istedi ama macOS'ta Canard için mikrofon izni kapalı.\n\nSistem Ayarları › "
                    "Gizlilik ve Güvenlik › Mikrofon'da Canard'ı aç. Değişiklik Canard yeniden açılınca geçerli olur.")
        self._front()
        box = QMessageBox(QMessageBox.Icon.Warning, title, text)
        settings = box.addButton("Ayarları Aç", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Sonra", QMessageBox.ButtonRole.RejectRole)
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
            self.show_error(f"Log klasörü henüz yok: {folder}")

    def _about(self) -> None:
        self._front()
        QMessageBox.about(None, "Canard hakkında", (
            f"<b>Canard {canard.__version__}</b> — CANvas boARD engine<br>"
            "Web içeriklerini canlı duvar kağıdı ve ekran koruyucusu olarak çalıştırır.<br>"
            "Lisans: GPL-3.0<br><br>"
            f"Ayarlar: {self._app.settings_path}<br>"
            f"Log: {logs.default_log_path()}"))

    def _front(self) -> None:
        if self._platform is not None:
            self._platform.bring_to_front()
