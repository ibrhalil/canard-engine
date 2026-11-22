"""Arayüz dili. Kaynak metinler İngilizcedir; diğer diller `canard/i18n/<kod>.py` içinde `CATALOG` sözlüğüdür.

Yalnız kullanıcıya görünen metinler çevrilir (menü, iletişim kutuları, CLI yardımı ve hataları, Canard.saver mesajları);
tanı logları İngilizce kalır.
"""

from importlib import import_module

LANGUAGES = {"en": "English", "tr": "Türkçe"}
SYSTEM = "system"

language = "en"
_catalog: dict[str, str] = {}


def system_language() -> str:
    from PySide6.QtCore import QLocale

    for tag in QLocale.system().uiLanguages():
        code = tag.replace("_", "-").split("-")[0].lower()
        if code in LANGUAGES:
            return code
    return "en"


def resolve(choice: str | None) -> str:
    """Ayar değeri ("system", "en", "tr" ya da None) → dil kodu."""
    return choice if choice in LANGUAGES else system_language()


def set_language(code: str) -> None:
    global language, _catalog
    language = code if code in LANGUAGES else "en"
    _catalog = {} if language == "en" else import_module(f"canard.i18n.{language}").CATALOG


def t(text: str, **values) -> str:
    text = _catalog.get(text, text)
    return text.format(**values) if values else text


def N_(text: str) -> str:
    """Yalnız işaretler: sabit tablolardaki metin çeviri kataloğunda olmalı, çeviri kullanıldığı yerde t() ile yapılır."""
    return text
