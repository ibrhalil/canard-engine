import sys
from functools import cache


class PlatformAdapter:
    name = "generic"

    def apply_wallpaper_window(self, widget, interactive: bool = False) -> None:
        raise NotImplementedError

    def install_hotkeys(self, actions: dict) -> list[str]:
        raise NotImplementedError

    def hotkey_keys(self) -> dict[str, str]:
        """Eylem adı → tuş ("K", "←"); hepsi ⌃⌥⌘ ile birlikte. Menü bunları öğelerin yanında gösterir."""
        raise NotImplementedError

    def watch_visibility(self, widget, callback):
        raise NotImplementedError

    def unwatch_visibility(self, token) -> None:
        raise NotImplementedError

    def create_system_audio(self, on_chunk, on_device_change):
        raise NotImplementedError

    # Ekran koruyucu (boşta tam ekran)
    def idle_seconds(self) -> float:
        raise NotImplementedError

    def display_sleep_blockers(self) -> list[str]:
        raise NotImplementedError

    def apply_screensaver_window(self, widget) -> None:
        raise NotImplementedError

    def focus_keeper(self):
        raise NotImplementedError

    def watch_session(self, callback):
        raise NotImplementedError

    # Ekran koruyucu kare yayını (Canard.saver)
    def screen_display_id(self, screen) -> int | None:
        raise NotImplementedError

    def window_visible(self, widget) -> bool:
        raise NotImplementedError

    def refresh_wallpaper_window(self, widget) -> None:
        """Pencerenin ekrandaki görüntüsünü tazeler (bkz. WallpaperManager._refresh_window)."""
        raise NotImplementedError

    def window_fingerprint(self, widget) -> bytes | None:
        """Pencerenin ekrandaki görüntüsünden küçük bir örnek: iki örnek aynıysa ekran eski karede takılı."""
        raise NotImplementedError

    def create_video_encoder(self, width: int, height: int, fps: int, bitrate: int, on_output):
        raise NotImplementedError

    def desktop_picture(self, display_id: int) -> dict | None:
        """Ekranın masaüstü resmi (settings.json'a yazılabilir sözlük); birebir geri konamayacaksa None."""
        raise NotImplementedError

    def set_desktop_picture(self, display_id: int, picture: dict) -> None:
        raise NotImplementedError

    def write_desktop_frame(self, image, path) -> dict:
        """Kareyi masaüstü resmi olacak dosyaya yazar; set_desktop_picture için sözlük döner."""
        raise NotImplementedError

    def app_integration(self):
        """Menü çubuğu uygulaması yardımcıları (oturum açılışı, Canard.saver kurulumu, izinler); bkz. macos/app.py."""
        raise NotImplementedError

    def before_gui(self) -> None:
        """QApplication oluşturulmadan hemen önce platforma özgü hazırlık (varsayılan: yok)."""


@cache
def detect() -> PlatformAdapter:
    if sys.platform == "darwin":
        from canard.platform.macos.adapter import MacAdapter

        return MacAdapter()
    raise NotImplementedError(f"platform not supported yet: {sys.platform}")
