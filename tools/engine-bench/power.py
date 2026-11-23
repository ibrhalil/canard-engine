"""Motor kıyası için sudo'suz güç ve ısı okuması (yalnız ölçüm aracı; Canard'a girmez).

- GPU enerjisi: libIOReport "Energy Model" › "GPU Energy" (nJ, sistem geneli). IOReport Apple'ın belgelenmemiş
  arayüzüdür (macmon gibi araçlar da kullanır); bu makinede (macOS 27, M2) sudo'suz yalnız GPU kanalı dolu geliyor,
  CPU kanalları 0 — CPU için süreç CPU süresi kullanılır.
- Isı durumu: NSProcessInfo.thermalState (0 normal · 1 orta · 2 ciddi · 3 kritik); fansız Mac'te yavaşlatma sinyali.
"""

import ctypes

from Foundation import NSProcessInfo

_vp = ctypes.c_void_p
_THERMAL = ["normal", "orta", "ciddi", "kritik"]


class GpuEnergy:
    """`read()` → başlangıçtan beri GPU enerjisi (J); okunamazsa None."""

    def __init__(self):
        self._ok = False
        try:
            lib = ctypes.CDLL("/usr/lib/libIOReport.dylib")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        except OSError:
            return
        cf.CFStringCreateWithCString.restype = _vp
        cf.CFStringCreateWithCString.argtypes = [_vp, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFRelease.argtypes = [_vp]
        lib.IOReportCopyChannelsInGroup.restype = _vp
        lib.IOReportCopyChannelsInGroup.argtypes = [_vp, _vp, ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint64]
        lib.IOReportCreateSubscription.restype = _vp
        lib.IOReportCreateSubscription.argtypes = [_vp, _vp, ctypes.POINTER(_vp), ctypes.c_uint64, _vp]
        lib.IOReportCreateSamples.restype = _vp
        lib.IOReportCreateSamples.argtypes = [_vp, _vp, _vp]
        lib.IOReportCreateSamplesDelta.restype = _vp
        lib.IOReportCreateSamplesDelta.argtypes = [_vp, _vp, _vp]
        lib.IOReportSimpleGetIntegerValue.restype = ctypes.c_int64
        lib.IOReportSimpleGetIntegerValue.argtypes = [_vp, ctypes.c_int32]
        for name in ("IOReportChannelGetChannelName", "IOReportChannelGetUnitLabel"):
            getattr(lib, name).restype = _vp
            getattr(lib, name).argtypes = [_vp]
        self._lib, self._cf = lib, cf
        group = cf.CFStringCreateWithCString(None, b"Energy Model", 0x08000100)  # kCFStringEncodingUTF8
        channels = lib.IOReportCopyChannelsInGroup(group, None, 0, 0, 0)
        cf.CFRelease(group)
        if not channels:
            return
        self._channels = _vp()
        self._subscription = lib.IOReportCreateSubscription(None, channels, ctypes.byref(self._channels), 0, None)
        if not self._subscription:
            return
        self._first = lib.IOReportCreateSamples(self._subscription, self._channels, None)
        self._ok = bool(self._first)

    def read(self) -> float | None:
        if not self._ok:
            return None
        import objc

        lib = self._lib
        now = lib.IOReportCreateSamples(self._subscription, self._channels, None)
        delta = lib.IOReportCreateSamplesDelta(self._first, now, None)
        self._cf.CFRelease(now)
        if not delta:
            return None
        try:
            for item in objc.objc_object(c_void_p=delta)["IOReportChannels"]:
                pointer = objc.pyobjc_id(item)
                if str(objc.objc_object(c_void_p=lib.IOReportChannelGetChannelName(pointer))) != "GPU Energy":
                    continue
                unit = str(objc.objc_object(c_void_p=lib.IOReportChannelGetUnitLabel(pointer)))
                scale = {"nJ": 1e-9, "uJ": 1e-6, "µJ": 1e-6, "mJ": 1e-3}.get(unit)
                if scale is None:
                    return None
                return lib.IOReportSimpleGetIntegerValue(pointer, 0) * scale
        finally:
            self._cf.CFRelease(delta)
        return None


def thermal_state() -> int:
    return int(NSProcessInfo.processInfo().thermalState())


def thermal_name(state: int | None) -> str:
    return "–" if state is None else _THERMAL[state] if 0 <= state < len(_THERMAL) else str(state)
