import logging

import objc
from AppKit import (
    NSNotificationCenter,
    NSScreen,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowDidChangeOcclusionStateNotification,
    NSWindowOcclusionStateVisible,
)
from Quartz import (
    CGDataProviderCopyData,
    CGImageGetDataProvider,
    CGRectNull,
    CGWindowLevelForKey,
    CGWindowListCopyWindowInfo,
    CGWindowListCreateImage,
    kCGDesktopIconWindowLevelKey,
    kCGWindowImageBoundsIgnoreFraming,
    kCGWindowImageNominalResolution,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenAboveWindow,
)

log = logging.getLogger("canard.wallpaper")


def _ns_window(widget):
    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def apply_wallpaper_behavior(widget, interactive: bool = False) -> None:
    window = _ns_window(widget)
    # Etkileşimli modda Finder'ın simge penceresi tıklamaları yakalamasın diye simgelerin üstüne çıkılır.
    offset = 1 if interactive else -1
    window.setLevel_(CGWindowLevelForKey(kCGDesktopIconWindowLevelKey) + offset)
    window.setHasShadow_(False)
    window.setIgnoresMouseEvents_(not interactive)
    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle
    )
    window.setCollectionBehavior_(behavior)
    window.orderFrontRegardless()


def observe_occlusion(widget, callback):
    window = _ns_window(widget)

    def on_change(_notification):
        visible = bool(window.occlusionState() & NSWindowOcclusionStateVisible)
        if not visible:
            log.debug("occluded notification, windows above: %s", _covering_windows(window))
        callback(visible)

    token = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
        NSWindowDidChangeOcclusionStateNotification, window, None, on_change
    )
    return token, on_change


def _covering_windows(window, limit: int = 10) -> str:
    """Tanı: "örtülü" bildiriminde pencerenin ≥%25'ini kaplayan üstteki pencereler."""
    try:
        frame = window.frame()
        main_height = NSScreen.screens()[0].frame().size.height  # Cocoa sol-alt → CG sol-üst
        left, top = frame.origin.x, main_height - frame.origin.y - frame.size.height
        right, bottom = left + frame.size.width, top + frame.size.height
        area = frame.size.width * frame.size.height or 1
        found = []
        for info in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenAboveWindow, window.windowNumber()) or ():
            bounds = info.get("kCGWindowBounds") or {}
            x, y = bounds.get("X", 0), bounds.get("Y", 0)
            overlap = (max(0, min(right, x + bounds.get("Width", 0)) - max(left, x))
                       * max(0, min(bottom, y + bounds.get("Height", 0)) - max(top, y)))
            if overlap / area >= 0.25:
                name = info.get("kCGWindowName") or ""
                found.append(f"{info.get('kCGWindowOwnerName', '?')}{f' «{name[:30]}»' if name else ''}"
                             f" layer={info.get('kCGWindowLayer')} %{100 * overlap / area:.0f}"
                             f" alpha={info.get('kCGWindowAlpha', 1):.2g}")
        return "; ".join(found[:limit]) + (f" (+{len(found) - limit})" if len(found) > limit else "") or "none"
    except Exception as error:  # tanı satırı uygulamayı asla düşürmesin
        return f"unreadable: {error!r}"


def reorder_window(widget) -> None:
    """AppKit düzeyinde kapatıp yeniden öne alır: WindowServer güncel içeriği bağlar, Qt görünürlüğü değişmez."""
    window = _ns_window(widget)
    window.orderOut_(None)
    window.orderFrontRegardless()


def window_fingerprint(widget) -> bytes | None:
    """Pencere sunucusunun bu pencere için gösterdiği görüntüden seyrek piksel örneği (1x çözünürlük, birkaç ms)."""
    image = CGWindowListCreateImage(CGRectNull, kCGWindowListOptionIncludingWindow, _ns_window(widget).windowNumber(),
                                    kCGWindowImageBoundsIgnoreFraming | kCGWindowImageNominalResolution)
    if image is None:
        return None
    return bytes(CGDataProviderCopyData(CGImageGetDataProvider(image)))[::4093]


def is_visible(widget) -> bool:
    return bool(_ns_window(widget).occlusionState() & NSWindowOcclusionStateVisible)


def display_id_for(screen) -> int | None:
    """QScreen → CGDirectDisplayID. Qt ekran adı NSScreen.localizedName; aynı adlılar konumla ayırt edilir."""
    geometry = screen.geometry()
    matches = [s for s in NSScreen.screens() if s.localizedName() == screen.name()]
    if len(matches) > 1:
        matches = [s for s in matches if int(s.frame().origin.x) == geometry.x()
                   and int(s.frame().size.width) == geometry.width()] or matches
    if not matches:
        return None
    return int(matches[0].deviceDescription()["NSScreenNumber"])


def remove_observer(token) -> None:
    observer, _block = token
    NSNotificationCenter.defaultCenter().removeObserver_(observer)
