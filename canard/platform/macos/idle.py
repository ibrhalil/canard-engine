"""Boşta tam ekran (`--screensaver-after`) için macOS yardımcıları: boşluk süresi, uyku engelleri, oturum olayları."""

import ctypes
import ctypes.util
import os
from collections.abc import Callable

import objc
import Quartz
from AppKit import (
    NSApplication,
    NSCursor,
    NSDistributedNotificationCenter,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWorkspace,
)
from Foundation import NSObject

_iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
_iokit.IOPMCopyAssertionsByProcess.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
_iokit.IOPMCopyAssertionsByProcess.restype = ctypes.c_int32
_cf.CFRelease.argtypes = [ctypes.c_void_p]

# macOS'un kendi ekran koruyucusunu da erteleyen kilit türleri (video oynatma, sunum vb.).
_DISPLAY_ASSERTIONS = {"PreventUserIdleDisplaySleep", "NoDisplaySleepAssertion"}


def idle_seconds() -> float:
    return Quartz.CGEventSourceSecondsSinceLastEventType(Quartz.kCGEventSourceStateCombinedSessionState,
                                                         Quartz.kCGAnyInputEventType)


def display_sleep_blockers() -> list[str]:
    """Ekranın boşta uyumasını engelleyen başka süreçlerin kilitleri ("süreç: ad"); kendi sürecimiz hariç."""
    ref = ctypes.c_void_p()
    if _iokit.IOPMCopyAssertionsByProcess(ctypes.byref(ref)) != 0 or not ref.value:
        return []
    by_process = objc.objc_object(c_void_p=ref.value)
    _cf.CFRelease(ref)
    own = os.getpid()
    blockers = []
    for pid, assertions in by_process.items():
        if int(pid) == own:
            continue
        for assertion in assertions:
            if assertion.get("AssertType") in _DISPLAY_ASSERTIONS:
                blockers.append(f"{assertion.get('Process Name', pid)}: {assertion.get('AssertName', '?')}")
    return blockers


def apply_screensaver_level(widget) -> None:
    window = objc.objc_object(c_void_p=int(widget.winId())).window()
    window.setLevel_(Quartz.CGWindowLevelForKey(Quartz.kCGScreenSaverWindowLevelKey))
    window.setIgnoresMouseEvents_(True)
    window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                  | NSWindowCollectionBehaviorFullScreenAuxiliary
                                  | NSWindowCollectionBehaviorStationary
                                  | NSWindowCollectionBehaviorIgnoresCycle)
    window.orderFrontRegardless()


class FocusKeeper:
    """Etkinleşince Canard'ı öne alır (imleç ancak öndeki uygulama gizleyebilir); bitince önceki uygulamayı geri getirir."""

    def __init__(self):
        self._previous = None
        self._cursor_hidden = False

    def enter(self) -> None:
        self._previous = NSWorkspace.sharedWorkspace().frontmostApplication()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if not self._cursor_hidden:
            NSCursor.hide()
            self._cursor_hidden = True

    def leave(self) -> None:
        if self._cursor_hidden:
            NSCursor.unhide()
            self._cursor_hidden = False
        previous, self._previous = self._previous, None
        if previous is not None and previous.processIdentifier() != os.getpid():
            previous.activateWithOptions_(0)


class _Observer(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(_Observer, self).init()
        if self is None:
            return None
        self.callback = callback
        return self

    def fire_(self, note):
        self.callback(str(note.name()))


class SessionWatcher:
    """Kilit, ekran uykusu ve sistem ekran koruyucu olaylarını tek geri çağrıya bağlar."""

    DISTRIBUTED = ("com.apple.screenIsLocked", "com.apple.screenIsUnlocked", "com.apple.screensaver.didstart")
    WORKSPACE = ("NSWorkspaceScreensDidSleepNotification", "NSWorkspaceScreensDidWakeNotification")

    def __init__(self, callback: Callable[[str], None]):
        self._observer = _Observer.alloc().initWithCallback_(callback)
        center = NSDistributedNotificationCenter.defaultCenter()
        for name in self.DISTRIBUTED:
            center.addObserver_selector_name_object_(self._observer, "fire:", name, None)
        workspace = NSWorkspace.sharedWorkspace().notificationCenter()
        for name in self.WORKSPACE:
            workspace.addObserver_selector_name_object_(self._observer, "fire:", name, None)

    def close(self) -> None:
        NSDistributedNotificationCenter.defaultCenter().removeObserver_(self._observer)
        NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self._observer)
