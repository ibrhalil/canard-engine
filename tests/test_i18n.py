import ast
import string
from pathlib import Path

import pytest

from canard import i18n
from canard.i18n import tr

PACKAGE = Path(__file__).resolve().parents[1] / "canard"


def _marked_strings() -> set[str]:
    found = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("t", "N_") and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


def _fields(text: str) -> set[str]:
    return {name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_every_marked_string_has_a_turkish_translation():
    missing = _marked_strings() - tr.CATALOG.keys()
    assert not missing, sorted(missing)


def test_catalog_has_no_unused_entries():
    unused = tr.CATALOG.keys() - _marked_strings()
    assert not unused, sorted(unused)


@pytest.mark.parametrize("source", sorted(tr.CATALOG))
def test_placeholders_match(source):
    assert _fields(source) == _fields(tr.CATALOG[source])


def test_translate_and_format():
    i18n.set_language("tr")
    try:
        assert i18n.t("Screen {number}", number=2) == "Ekran 2"
        assert i18n.t("not in catalog") == "not in catalog"
    finally:
        i18n.set_language("en")
    assert i18n.t("Screen {number}", number=2) == "Screen 2"


def test_resolve_explicit_choice():
    assert i18n.resolve("tr") == "tr"
    assert i18n.resolve("en") == "en"
    assert i18n.resolve("system") in i18n.LANGUAGES
