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
from Quartz import CGWindowLevelForKey, kCGDesktopIconWindowLevelKey


def _ns_window(widget):
    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def apply_wallpaper_behavior(widget, interactive: bool = False) -> None:
    window = _ns_window(widget)
    # Pasif: simgelerin altında. Etkileşimli: Finder'ın simge penceresi boş masaüstü
    # tıklamalarını yakaladığı için simgelerin üstüne çıkılır (simgeler bu sırada örtülür).
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
        callback(bool(window.occlusionState() & NSWindowOcclusionStateVisible))

    token = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
        NSWindowDidChangeOcclusionStateNotification, window, None, on_change
    )
    return token, on_change


def is_visible(widget) -> bool:
    return bool(_ns_window(widget).occlusionState() & NSWindowOcclusionStateVisible)


def display_id_for(screen) -> int | None:
    """QScreen → CGDirectDisplayID (Canard.saver ekranını bununla bildirir). Qt ekran adı NSScreen.localizedName'dir;
    aynı adlı ekranlarda konum (Qt sol-üst, Cocoa sol-alt) ile ayırt edilir."""
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
