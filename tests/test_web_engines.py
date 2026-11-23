import json
import sys

from canard import web
from canard.web.bridge import HostBridge


def test_engines_per_platform():
    expected = [web.WEBKIT, web.CHROMIUM] if sys.platform == "darwin" else [web.CHROMIUM]
    assert web.available_engines() == expected
    assert web.default_engine() == expected[0]


def test_resolve_engine_falls_back_to_default():
    assert web.resolve_engine(web.CHROMIUM) == web.CHROMIUM
    assert web.resolve_engine(None) == web.default_engine()
    assert web.resolve_engine("gecko") == web.default_engine()


def test_bridge_payload_names_the_renderer():
    source = HostBridge(mode="wallpaper", source={"id": "x"}, system_audio=True, renderer=web.WEBKIT).script_source()
    assert source.startswith("window.canard = Object.freeze(") and source.endswith(");")
    payload = json.loads(source.removeprefix("window.canard = Object.freeze(").removesuffix(");"))
    assert payload["engine"] == "canard"
    assert payload["renderer"] == web.WEBKIT
    assert payload["mode"] == "wallpaper"
    assert payload["systemAudio"] is True
