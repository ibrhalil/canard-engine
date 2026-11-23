import json

from canard import __version__


class HostBridge:
    """`window.canard`: içerik Canard'da çalıştığını, modu, kaynağını ve motoru buradan okuyabilir."""

    def __init__(self, mode: str, source: dict | None = None, system_audio: bool = False, renderer: str = ""):
        self.mode = mode
        self.source = source
        self.system_audio = system_audio
        self.renderer = renderer

    def script_source(self) -> str:
        payload = {
            "engine": "canard",
            "version": __version__,
            "renderer": self.renderer,
            "mode": self.mode,
            "source": self.source,
            "systemAudio": self.system_audio,
        }
        return "window.canard = Object.freeze(%s);" % json.dumps(payload, ensure_ascii=False)
