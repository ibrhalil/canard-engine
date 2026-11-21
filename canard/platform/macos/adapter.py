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

    def unwatch_visibility(self, token) -> None:
        from canard.platform.macos.wallpaper import remove_observer

        remove_observer(token)
