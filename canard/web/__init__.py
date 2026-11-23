"""Web motorları. macOS'ta iki seçenek: WebKit (WKWebView, sistemin motoru, daha az GPU/enerji) ve Chromium
(QtWebEngine, Chromium'a özgü API'ler için). Diğer platformlarda yalnız Chromium. Seçilmeyen motor içe aktarılmaz."""

import sys

ENGINE_KEY = "web.engine"  # settings.json; menü değiştirir, komut satırında --engine bu çalıştırma için geçersiz kılar
WEBKIT = "webkit"
CHROMIUM = "chromium"
LABELS = {WEBKIT: "WebKit", CHROMIUM: "Chromium"}


def available_engines() -> list[str]:
    if sys.platform == "darwin":
        return [WEBKIT, CHROMIUM]
    return [CHROMIUM]


def default_engine() -> str:
    return available_engines()[0]


def resolve_engine(choice) -> str:
    """Ayar ya da komut satırı değeri → kullanılabilir motor adı."""
    return choice if choice in available_engines() else default_engine()


def create_engine(name: str):
    if name == WEBKIT and WEBKIT in available_engines():
        from canard.web.webkit.engine import WebKitEngine

        return WebKitEngine()
    if name == CHROMIUM:
        from canard.web.chromium.engine import ChromiumEngine

        return ChromiumEngine()
    raise ValueError(f"web engine not available: {name}")
