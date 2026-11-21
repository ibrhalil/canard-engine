import objc
from AppKit import (
    NSNotificationCenter,
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


def remove_observer(token) -> None:
    observer, _block = token
    NSNotificationCenter.defaultCenter().removeObserver_(observer)
