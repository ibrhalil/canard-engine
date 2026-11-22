"""Carbon RegisterEventHotKey ile global kısayollar (ctypes).

pyobjc-framework-Carbon 13 yalnızca RegisterEventHotKey'i sarar; InstallEventHandler
(C callback) yoktur. Bu yüzden Carbon'a doğrudan ctypes ile bağlanılır. Carbon hotkey'leri
Erişilebilirlik izni istemez ve NSApplication olay döngüsünde (Qt Cocoa) teslim edilir.
"""

import ctypes
import ctypes.util
import logging
from collections.abc import Callable

_carbon = ctypes.CDLL(ctypes.util.find_library("Carbon"))

OSStatus = ctypes.c_int32
EventHandlerProcPtr = ctypes.CFUNCTYPE(OSStatus, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


kEventClassKeyboard = _fourcc("keyb")
kEventHotKeyPressed = 5
kEventParamDirectObject = _fourcc("----")
typeEventHotKeyID = _fourcc("hkid")
_SIGNATURE = _fourcc("cnrd")

CMD = 1 << 8
SHIFT = 1 << 9
OPTION = 1 << 11
CONTROL = 1 << 12

KEY_LEFT = 0x7B
KEY_RIGHT = 0x7C
# Sanal tuş kodları fiziksel konumdur (kVK_ANSI_*).
KEY_H = 0x04
KEY_Y = 0x10
KEY_P = 0x23
KEY_L = 0x25
KEY_K = 0x28
KEY_N = 0x2D

# ⌘⌥ JetBrains ve Chrome ile çakışıyor; ⌃⌥⌘ sol elde, harfler klavyenin sağ yarısından.
_HYPER = CONTROL | OPTION | CMD
_PREFIX = "⌃ + ⌥ + ⌘ + "
DEFAULT_BINDINGS = {
    "prev": (KEY_LEFT, _HYPER, _PREFIX + "←"),
    "next": (KEY_RIGHT, _HYPER, _PREFIX + "→"),
    "toggle_interactive": (KEY_K, _HYPER, _PREFIX + "K"),
    "screensaver_now": (KEY_L, _HYPER, _PREFIX + "L"),
    "toggle_pause": (KEY_P, _HYPER, _PREFIX + "P"),
    "toggle_wallpaper": (KEY_H, _HYPER, _PREFIX + "H"),
    "reload": (KEY_Y, _HYPER, _PREFIX + "Y"),
    "next_source": (KEY_N, _HYPER, _PREFIX + "N"),
}

_carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
_carbon.GetApplicationEventTarget.argtypes = []
_carbon.RegisterEventHotKey.restype = OSStatus
_carbon.RegisterEventHotKey.argtypes = [
    ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_void_p),
]
_carbon.UnregisterEventHotKey.restype = OSStatus
_carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
_carbon.InstallEventHandler.restype = OSStatus
_carbon.InstallEventHandler.argtypes = [
    ctypes.c_void_p, EventHandlerProcPtr, ctypes.c_ulong, ctypes.POINTER(EventTypeSpec),
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
]
_carbon.RemoveEventHandler.restype = OSStatus
_carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
_carbon.GetEventParameter.restype = OSStatus
_carbon.GetEventParameter.argtypes = [
    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_ulong,
    ctypes.c_void_p, ctypes.c_void_p,
]


class HotkeyRegistry:
    def __init__(self):
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._refs: list[ctypes.c_void_p] = []
        self._handler_ref = ctypes.c_void_p()
        self._proc = EventHandlerProcPtr(self._on_event)
        spec = EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
        status = _carbon.InstallEventHandler(
            _carbon.GetApplicationEventTarget(), self._proc, 1, ctypes.byref(spec),
            None, ctypes.byref(self._handler_ref),
        )
        if status != 0:
            raise OSError(f"InstallEventHandler failed: {status}")

    def register(self, key_code: int, modifiers: int, callback: Callable[[], None]) -> bool:
        hotkey_id = len(self._callbacks) + 1
        ref = ctypes.c_void_p()
        status = _carbon.RegisterEventHotKey(
            key_code, modifiers, EventHotKeyID(_SIGNATURE, hotkey_id),
            _carbon.GetApplicationEventTarget(), 0, ctypes.byref(ref),
        )
        if status != 0:
            return False
        self._callbacks[hotkey_id] = callback
        self._refs.append(ref)
        return True

    def close(self) -> None:
        for ref in self._refs:
            _carbon.UnregisterEventHotKey(ref)
        self._refs.clear()
        self._callbacks.clear()
        if self._handler_ref:
            _carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()

    def _on_event(self, _call_ref, event, _user_data) -> int:
        hotkey = EventHotKeyID()
        status = _carbon.GetEventParameter(
            event, kEventParamDirectObject, typeEventHotKeyID, None,
            ctypes.sizeof(hotkey), None, ctypes.byref(hotkey),
        )
        if status == 0 and hotkey.signature == _SIGNATURE:
            callback = self._callbacks.get(hotkey.id)
            if callback is not None:
                try:
                    callback()
                except Exception as error:
                    logging.getLogger("canard.hotkey").exception("shortcut handler failed: %r", error)
        return 0
