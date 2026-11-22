import json

from canard import __version__

SCRIPT_NAME = "canard-host-bridge"


def remove_scripts(profile, name: str) -> None:
    """Profilden bu adlı betikleri kaldırır (yeni yüklenen sayfalarda geçerli)."""
    scripts = profile.scripts()
    for script in scripts.find(name):
        scripts.remove(script)


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

        remove_scripts(profile, SCRIPT_NAME)
        script = QWebEngineScript()
        script.setName(SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(self.script_source())
        profile.scripts().insert(script)
