from PySide6.QtWebEngineCore import QWebEngineScript


def install(profile, name: str, source: str) -> None:
    """Profildeki aynı adlı betiğin yerine geçer; belge oluşturulurken, ana çerçevede, sayfanın kendi dünyasında
    çalışır (yeni yüklenen sayfalarda geçerli)."""
    remove(profile, name)
    script = QWebEngineScript()
    script.setName(name)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    script.setSourceCode(source)
    profile.scripts().insert(script)


def remove(profile, name: str) -> None:
    scripts = profile.scripts()
    for script in scripts.find(name):
        scripts.remove(script)
