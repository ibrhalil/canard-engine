"""Faz 4 spike — Core Audio Process Tap (macOS 14.2+) ile sistem sesi yakalama denemesi.

Çalıştır:  uv run python tools/audio-probe/tap_probe.py
Müzik çalarken terminalde seviye çubuğu oynamalı. Ctrl+C ile çık.

Tap + özel aggregate device kurulur; IOProc ctypes ile bağlanır (gerçek zamanlı geri çağrıda
pyobjc AudioBufferList işaretçisiyle uğraşmamak için).
"""

import ctypes
import ctypes.util
import os
import signal
import struct
import sys
import threading
import time
import uuid

import objc
import CoreAudio as CA
from Foundation import NSArray, NSNumber, NSPropertyListBinaryFormat_v1_0, NSPropertyListSerialization

sys.path.insert(0, os.path.dirname(__file__))
from meter import Meter  # noqa: E402

_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))


class AudioBuffer(ctypes.Structure):
    _fields_ = [("mNumberChannels", ctypes.c_uint32), ("mDataByteSize", ctypes.c_uint32),
                ("mData", ctypes.c_void_p)]


class AudioBufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", AudioBuffer * 1)]


IOProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ca.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, IOProc, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
_ca.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
_ca.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceStart.restype = ctypes.c_int32
_ca.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceStop.restype = ctypes.c_int32
_ca.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceDestroyIOProcID.restype = ctypes.c_int32
# CFDictionaryRef parametresi pyobjc'de opak struct işaretçisi olarak tanımlı; ctypes ile gerçek işaretçi verilir.
_ca.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ca.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32


def _addr(selector):
    return CA.AudioObjectPropertyAddress(selector, CA.kAudioObjectPropertyScopeGlobal,
                                         CA.kAudioObjectPropertyElementMain)


def _get(obj_id, selector, size, qualifier=None):
    q_size, q_data = (len(qualifier), qualifier) if qualifier else (0, objc.NULL)
    status, _size, data = CA.AudioObjectGetPropertyData(obj_id, _addr(selector), q_size, q_data, size, None)
    if status != 0:
        raise OSError(f"AudioObjectGetPropertyData({selector}) = {status}")
    return data


def _k(key) -> str:
    # pyobjc aggregate/tap sözlük anahtarlarını (#define "uid" vb.) bytes verir; CoreAudio CFString bekler,
    # bytes anahtar CFData olur ve coreaudio SEGV ile çöker.
    return key.decode() if isinstance(key, bytes) else key


def _plist(value):
    # İç içe Python list/dict pyobjc vekil nesnesi olur; plist turu saf Foundation nesneleri üretir.
    data, error = NSPropertyListSerialization.dataWithPropertyList_format_options_error_(
        value, NSPropertyListBinaryFormat_v1_0, 0, None)
    if data is None:
        raise ValueError(f"Geçersiz aggregate sözlüğü: {error}")
    plist, _fmt, error = NSPropertyListSerialization.propertyListWithData_options_format_error_(data, 0, None, None)
    return plist


def _check(status, what):
    if status != 0:
        raise OSError(f"{what} başarısız: OSStatus {status}")


def main() -> int:
    own_proc = struct.unpack("I", _get(CA.kAudioObjectSystemObject,
                                       CA.kAudioHardwarePropertyTranslatePIDToProcessObject, 4,
                                       struct.pack("i", os.getpid())))[0]
    out_dev = struct.unpack("I", _get(CA.kAudioObjectSystemObject,
                                      CA.kAudioHardwarePropertyDefaultSystemOutputDevice, 4))[0]
    out_uid = str(objc.objc_object(c_void_p=struct.unpack("Q", _get(out_dev, CA.kAudioDevicePropertyDeviceUID, 8))[0]))
    print(f"Varsayılan çıkış: {out_uid}")

    excluded = NSArray.arrayWithArray_([NSNumber.numberWithUnsignedInt_(own_proc)] if own_proc else [])
    desc = CA.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(excluded)
    desc.setName_("Canard probe tap")
    desc.setPrivate_(True)
    status, tap_id = CA.AudioHardwareCreateProcessTap(desc, None)
    _check(status, "AudioHardwareCreateProcessTap")

    asbd = _get(tap_id, CA.kAudioTapPropertyFormat, 40)
    rate, fmt_id, flags, _bpp, _fpp, bpf, channels, bits, _r = struct.unpack("dIIIIIIII", asbd)
    non_interleaved = bool(flags & (1 << 5))
    print(f"Tap formatı: {rate:.0f} Hz, {channels} kanal, {bits} bit, "
          f"{'planar' if non_interleaved else 'interleaved'}, flags=0x{flags:x}")

    agg_uid = f"canard.probe.{uuid.uuid4()}"
    agg_dict = _plist({
        _k(CA.kAudioAggregateDeviceNameKey): "Canard Probe",
        _k(CA.kAudioAggregateDeviceUIDKey): agg_uid,
        _k(CA.kAudioAggregateDeviceMainSubDeviceKey): out_uid,
        _k(CA.kAudioAggregateDeviceIsPrivateKey): True,
        _k(CA.kAudioAggregateDeviceIsStackedKey): False,
        _k(CA.kAudioAggregateDeviceTapAutoStartKey): True,
        _k(CA.kAudioAggregateDeviceSubDeviceListKey): [{_k(CA.kAudioSubDeviceUIDKey): out_uid}],
        _k(CA.kAudioAggregateDeviceTapListKey): [
            {_k(CA.kAudioSubTapDriftCompensationKey): True, _k(CA.kAudioSubTapUIDKey): str(desc.UUID().UUIDString())}
        ],
    })
    agg_ref = ctypes.c_uint32()
    status = _ca.AudioHardwareCreateAggregateDevice(objc.pyobjc_id(agg_dict), ctypes.byref(agg_ref))
    agg_id = agg_ref.value
    if status != 0:
        CA.AudioHardwareDestroyProcessTap(tap_id)
        _check(status, "AudioHardwareCreateAggregateDevice")

    meter = Meter()
    callbacks = [0]

    def io_proc(_dev, _now, in_data, _in_time, _out_data, _out_time, _client):
        callbacks[0] += 1
        if not in_data:
            return 0
        abl = ctypes.cast(in_data, ctypes.POINTER(AudioBufferList)).contents
        buffers = ctypes.cast(ctypes.addressof(abl.mBuffers), ctypes.POINTER(AudioBuffer * abl.mNumberBuffers)).contents
        for buf in buffers:
            if buf.mData and buf.mDataByteSize:
                count = buf.mDataByteSize // 4
                meter.feed((ctypes.c_float * count).from_address(buf.mData))
        return 0

    proc = IOProc(io_proc)
    proc_id = ctypes.c_void_p()
    stop = threading.Event()
    try:
        _check(_ca.AudioDeviceCreateIOProcID(agg_id, proc, None, ctypes.byref(proc_id)), "AudioDeviceCreateIOProcID")
        _check(_ca.AudioDeviceStart(agg_id, proc_id), "AudioDeviceStart")
        print("Yakalama başladı — müzik çal. Ctrl+C ile çık.\n")
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        while not stop.is_set():
            time.sleep(0.1)
            meter.render(extra=f"geri çağrı={callbacks[0]}")
    finally:
        print()
        if proc_id:
            _ca.AudioDeviceStop(agg_id, proc_id)
            _ca.AudioDeviceDestroyIOProcID(agg_id, proc_id)
        CA.AudioHardwareDestroyAggregateDevice(agg_id)
        CA.AudioHardwareDestroyProcessTap(tap_id)
        print(meter.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
