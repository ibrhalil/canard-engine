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

    def watch_visibility(self, widget, callback):
        raise NotImplementedError

    def unwatch_visibility(self, token) -> None:
        raise NotImplementedError

    def create_system_audio(self, on_chunk, on_device_change):
        raise NotImplementedError

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
