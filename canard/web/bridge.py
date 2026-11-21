import json

from canard import __version__


class HostBridge:
    def __init__(self, mode: str, source: dict | None = None, system_audio: bool = False):
        self.mode = mode
        self.source = source
        self.system_audio = system_audio

    def script_source(self) -> str:
        payload = {
            "engine": "canard",
            "version": __version__,
            "mode": self.mode,
            "source": self.source,
            "systemAudio": self.system_audio,
        }
        return "window.canard = Object.freeze(%s);" % json.dumps(payload, ensure_ascii=False)

    def install(self, profile) -> None:
        from PySide6.QtWebEngineCore import QWebEngineScript

        script = QWebEngineScript()
        script.setName("canard-host-bridge")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(self.script_source())
        profile.scripts().insert(script)
