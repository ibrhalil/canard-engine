"""Masaüstü resmi: okuma, kareyi yazma, ayarlama ve geri koyma (NSWorkspace).

Resim ayarlarken içerik `{"url": ..., "options": {...}}` sözlüğüdür (settings.json'a yazılabilir). Geri koyma yalnız
masaüstü durağan bir resim dosyasıyken birebirdir: uygulamalar sistemin renk / hareketli duvar kağıdı seçimlerine
belgeli yoldan dönemez; o durumda `current()` None döner ve masaüstüne hiç dokunulmaz.
"""

import plistlib
from pathlib import Path

import Quartz
from AppKit import (
    NSColor,
    NSScreen,
    NSWorkspace,
    NSWorkspaceDesktopImageAllowClippingKey,
    NSWorkspaceDesktopImageFillColorKey,
    NSWorkspaceDesktopImageScalingKey,
)
from Foundation import NSURL

# Sistem Ayarları'nın seçimi (renk / resim / hareketli). Yalnız okunur; belgelenmemiş, okunamazsa dokunulmaz.
_STORE = Path.home() / "Library/Application Support/com.apple.wallpaper/Store/Index.plist"
_IMAGE_PROVIDER = "com.apple.wallpaper.choice.image"


def _screen(display_id: int):
    screen = next((s for s in NSScreen.screens() if s.deviceDescription()["NSScreenNumber"] == display_id), None)
    if screen is None:
        raise RuntimeError(f"no screen with display id {display_id}")
    return screen


def _last_choice_provider() -> str | None:
    """En son ayarlanan masaüstü seçiminin türü."""
    try:
        with _STORE.open("rb") as file:
            store = plistlib.load(file)
    except (OSError, plistlib.InvalidFileException):
        return None
    latest = (None, None)

    def walk(node):
        nonlocal latest
        if not isinstance(node, dict):
            return
        desktop = node.get("Desktop")
        if isinstance(desktop, dict):
            choices = desktop.get("Content", {}).get("Choices") or []
            when = desktop.get("LastSet")
            if choices and when is not None and (latest[0] is None or when > latest[0]):
                latest = (when, choices[0].get("Provider"))
        for value in node.values():
            walk(value)

    walk(store)
    return latest[1]


def current(display_id: int) -> dict | None:
    """Ekranın masaüstü resmi; durağan bir resim dosyası değilse None."""
    if _last_choice_provider() != _IMAGE_PROVIDER:
        return None
    workspace = NSWorkspace.sharedWorkspace()
    screen = _screen(display_id)
    url = workspace.desktopImageURLForScreen_(screen)
    if url is None or not url.isFileURL() or not Path(url.path()).is_file():
        return None
    options = {}
    raw = workspace.desktopImageOptionsForScreen_(screen) or {}
    if raw.get(NSWorkspaceDesktopImageScalingKey) is not None:
        options["scaling"] = int(raw[NSWorkspaceDesktopImageScalingKey])
    if raw.get(NSWorkspaceDesktopImageAllowClippingKey) is not None:
        options["clipping"] = bool(raw[NSWorkspaceDesktopImageAllowClippingKey])
    color = raw.get(NSWorkspaceDesktopImageFillColorKey)
    if color is not None:
        color = color.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
        if color is not None:
            options["fill"] = [color.redComponent(), color.greenComponent(), color.blueComponent(),
                               color.alphaComponent()]
    return {"url": url.path(), "options": options}


def apply(display_id: int, picture: dict) -> None:
    options = {}
    given = picture.get("options") or {}
    if "scaling" in given:
        options[NSWorkspaceDesktopImageScalingKey] = given["scaling"]
    if "clipping" in given:
        options[NSWorkspaceDesktopImageAllowClippingKey] = given["clipping"]
    if "fill" in given:
        options[NSWorkspaceDesktopImageFillColorKey] = NSColor.colorWithCalibratedRed_green_blue_alpha_(*given["fill"])
    ok, error = NSWorkspace.sharedWorkspace().setDesktopImageURL_forScreen_options_error_(
        NSURL.fileURLWithPath_(picture["url"]), _screen(display_id), options, None)
    if not ok:
        raise RuntimeError(f"setDesktopImageURL failed: {error}")


def write_frame(image, path: Path) -> dict:
    """image: QImage ya da CGImage → JPEG; `apply` için içerik."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(image, "save"):  # QImage
        if not image.save(str(path), "JPG", 90):
            raise RuntimeError(f"could not write {path}")
    else:
        destination = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(str(path)), "public.jpeg", 1,
                                                             None)
        if destination is None:
            raise RuntimeError(f"could not write {path}")
        Quartz.CGImageDestinationAddImage(destination, image,
                                          {Quartz.kCGImageDestinationLossyCompressionQuality: 0.9})
        if not Quartz.CGImageDestinationFinalize(destination):
            raise RuntimeError(f"could not write {path}")
    return {"url": str(path), "options": {}}
