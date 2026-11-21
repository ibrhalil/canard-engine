from canard.platform.base import PlatformAdapter


class MacAdapter(PlatformAdapter):
    name = "macos"

    def __init__(self):
        self._hotkeys = None

    def apply_wallpaper_window(self, widget, interactive: bool = False) -> None:
        from canard.platform.macos.wallpaper import apply_wallpaper_behavior

        apply_wallpaper_behavior(widget, interactive=interactive)

    def install_hotkeys(self, actions: dict) -> list[str]:
        from canard.platform.macos.hotkeys import DEFAULT_BINDINGS, HotkeyRegistry

        if self._hotkeys is None:
            self._hotkeys = HotkeyRegistry()
        registered = []
        for name, callback in actions.items():
            binding = DEFAULT_BINDINGS.get(name)
            if binding is None:
                continue
            key_code, modifiers, label = binding
            if self._hotkeys.register(key_code, modifiers, callback):
                registered.append(f"{label} → {name}")
        return registered

    def watch_visibility(self, widget, callback):
        from canard.platform.macos.wallpaper import observe_occlusion

        return observe_occlusion(widget, callback)

    def create_system_audio(self, on_chunk, on_device_change):
        from canard.platform.macos.audio_tap import SystemAudioTap

        return SystemAudioTap(on_chunk, on_device_change)

    def idle_seconds(self) -> float:
        from canard.platform.macos.idle import idle_seconds

        return idle_seconds()

    def display_sleep_blockers(self) -> list[str]:
        from canard.platform.macos.idle import display_sleep_blockers

        return display_sleep_blockers()

    def apply_screensaver_window(self, widget) -> None:
        from canard.platform.macos.idle import apply_screensaver_level

        apply_screensaver_level(widget)

    def focus_keeper(self):
        from canard.platform.macos.idle import FocusKeeper

        return FocusKeeper()

    def watch_session(self, callback):
        from canard.platform.macos.idle import SessionWatcher

        return SessionWatcher(callback)

    def screen_display_id(self, screen) -> int | None:
        from canard.platform.macos.wallpaper import display_id_for

        return display_id_for(screen)

    def window_visible(self, widget) -> bool:
        from canard.platform.macos.wallpaper import is_visible

        return is_visible(widget)

    def create_video_encoder(self, width: int, height: int, fps: int, bitrate: int, on_output):
        from canard.platform.macos.video import HevcEncoder

        return HevcEncoder(width, height, fps, bitrate, on_output)

    def unwatch_visibility(self, token) -> None:
        from canard.platform.macos.wallpaper import remove_observer

        remove_observer(token)
