import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenInfo:
    name: str
    x: int
    y: int
    width: int
    height: int
    device_pixel_ratio: float


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

    def create_video_encoder(self, width: int, height: int, fps: int, bitrate: int, on_output):
        raise NotImplementedError

    # Menü çubuğu uygulaması (canard --tray): arka plan uygulaması, oturum açılışı, ekran koruyucu kurulumu.
    # Dönen nesne: set_accessory, bring_to_front, login_item_enabled, set_login_item, saver_installed,
    # saver_package, install_saver, open_screensaver_settings, open_lock_screen_settings, start_screensaver,
    # screen_lock_delay, permission_status, open_privacy_settings (bkz. canard/platform/macos/app.py).
    def app_integration(self):
        raise NotImplementedError

    def before_gui(self) -> None:
        """QApplication oluşturulmadan hemen önce platforma özgü hazırlık (varsayılan: yok)."""

    def screens(self) -> list[ScreenInfo]:
        from PySide6.QtGui import QGuiApplication

        result = []
        for screen in QGuiApplication.screens():
            geometry = screen.geometry()
            result.append(
                ScreenInfo(
                    name=screen.name(),
                    x=geometry.x(),
                    y=geometry.y(),
                    width=geometry.width(),
                    height=geometry.height(),
                    device_pixel_ratio=screen.devicePixelRatio(),
                )
            )
        return result


def detect() -> PlatformAdapter:
    if sys.platform == "darwin":
        from canard.platform.macos.adapter import MacAdapter

        return MacAdapter()
    raise NotImplementedError(f"Platform henüz desteklenmiyor: {sys.platform}")
