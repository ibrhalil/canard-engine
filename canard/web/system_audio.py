from pathlib import Path

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtWebEngineCore import QWebEngineScript

_SHIM = Path(__file__).with_name("system_audio.js")
SCRIPT_NAME = "canard-system-audio"


def _qwebchannel_source() -> str:
    resource = QFile(":/qtwebchannel/qwebchannel.js")
    if not resource.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError("qwebchannel.js Qt kaynağı bulunamadı")
    try:
        return bytes(resource.readAll()).decode("utf-8")
    finally:
        resource.close()


def script_source() -> str:
    # qwebchannel.js ve köprü tek IIFE'de: QWebChannel sayfanın global alanına sızmaz.
    return "(function () {\n%s\n%s\n})();" % (_qwebchannel_source(), _SHIM.read_text(encoding="utf-8"))


def install(profile) -> None:
    uninstall(profile)
    script = QWebEngineScript()
    script.setName(SCRIPT_NAME)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(script_source())
    profile.scripts().insert(script)


def uninstall(profile) -> None:
    from canard.web.bridge import remove_scripts

    remove_scripts(profile, SCRIPT_NAME)
