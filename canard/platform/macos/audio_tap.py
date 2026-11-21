"""Core Audio Process Tap (macOS 14.2+) ile sistem sesi yakalama.

Global stereo tap (kendi süreci hariç) + özel aggregate device kurulur; IOProc ctypes ile bağlanır.
Varsayılan çıkış aygıtı değişince (kulaklık takıldı/çıktı) tap yeniden kurulur.

pyobjc tuzakları (tools/audio-probe spike'ında bulundu):
- aggregate/tap sözlük anahtarı sabitleri bytes gelir; CFString'e çevrilmezse coreaudio SEGV verir,
- iç içe Python list/dict vekil nesne olur; plist turuyla saf Foundation nesnesine çevrilir,
- AudioHardwareCreateAggregateDevice'ın CFDictionaryRef parametresi ctypes ile verilir.
"""

import ctypes
import ctypes.util
import os
import struct
import threading
import uuid
from collections.abc import Callable

import objc
import CoreAudio as CA
from Foundation import NSArray, NSNumber, NSPropertyListBinaryFormat_v1_0, NSPropertyListSerialization

_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))


class _AudioBuffer(ctypes.Structure):
    _fields_ = [("mNumberChannels", ctypes.c_uint32), ("mDataByteSize", ctypes.c_uint32),
                ("mData", ctypes.c_void_p)]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [("mNumberBuffers", ctypes.c_uint32), ("mBuffers", _AudioBuffer * 1)]


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


_IOProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ListenerProc = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint32,
                                 ctypes.POINTER(_PropertyAddress), ctypes.c_void_p)

_ca.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, _IOProc, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
_ca.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
_ca.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceStart.restype = ctypes.c_int32
_ca.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceStop.restype = ctypes.c_int32
_ca.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
_ca.AudioDeviceDestroyIOProcID.restype = ctypes.c_int32
_ca.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ca.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
_ca.AudioObjectAddPropertyListener.argtypes = [ctypes.c_uint32, ctypes.POINTER(_PropertyAddress),
                                               _ListenerProc, ctypes.c_void_p]
_ca.AudioObjectAddPropertyListener.restype = ctypes.c_int32
_ca.AudioObjectRemovePropertyListener.argtypes = _ca.AudioObjectAddPropertyListener.argtypes
_ca.AudioObjectRemovePropertyListener.restype = ctypes.c_int32

_K_FORMAT_FLAG_NON_INTERLEAVED = 1 << 5


class AudioFormat:
    def __init__(self, sample_rate: float, channels: int, interleaved: bool):
        self.sample_rate = sample_rate
        self.channels = channels
        self.interleaved = interleaved


def _addr(selector):
    return CA.AudioObjectPropertyAddress(selector, CA.kAudioObjectPropertyScopeGlobal,
                                         CA.kAudioObjectPropertyElementMain)


def _get(obj_id, selector, size, qualifier=None) -> bytes:
    q_size, q_data = (len(qualifier), qualifier) if qualifier else (0, objc.NULL)
    status, _size, data = CA.AudioObjectGetPropertyData(obj_id, _addr(selector), q_size, q_data, size, None)
    if status != 0:
        raise OSError(f"AudioObjectGetPropertyData({selector}) = {status}")
    return data


def _check(status, what) -> None:
    if status != 0:
        raise OSError(f"{what} başarısız: OSStatus {status}")


def _k(key) -> str:
    return key.decode() if isinstance(key, bytes) else key


def _plist(value):
    data, error = NSPropertyListSerialization.dataWithPropertyList_format_options_error_(
        value, NSPropertyListBinaryFormat_v1_0, 0, None)
    if data is None:
        raise ValueError(f"Geçersiz aggregate sözlüğü: {error}")
    plist, _fmt, _error = NSPropertyListSerialization.propertyListWithData_options_format_error_(data, 0, None, None)
    return plist


def _default_output_uid() -> str:
    device = struct.unpack("I", _get(CA.kAudioObjectSystemObject,
                                     CA.kAudioHardwarePropertyDefaultSystemOutputDevice, 4))[0]
    pointer = struct.unpack("Q", _get(device, CA.kAudioDevicePropertyDeviceUID, 8))[0]
    return str(objc.objc_object(c_void_p=pointer))


def _own_process_object() -> int:
    return struct.unpack("I", _get(CA.kAudioObjectSystemObject,
                                   CA.kAudioHardwarePropertyTranslatePIDToProcessObject, 4,
                                   struct.pack("i", os.getpid())))[0]


class SystemAudioTap:
    """Sistem sesini yakalar; her IOProc'ta `on_chunk(bytes)` (float32 PCM) çağrılır.

    `on_chunk` CoreAudio'nun gerçek zamanlı iş parçacığında çalışır: kısa tutulmalı.
    `on_device_change` CoreAudio iş parçacığında çağrılır; yeniden kurulum çağıranın işidir.
    """

    def __init__(self, on_chunk: Callable[[bytes], None], on_device_change: Callable[[], None] | None = None):
        self._on_chunk = on_chunk
        self._on_device_change = on_device_change
        self._lock = threading.Lock()
        self._tap_id = 0
        self._agg_id = 0
        self._proc_id = ctypes.c_void_p()
        self._io_proc = _IOProc(self._io)
        self._listener = _ListenerProc(self._device_changed)
        self._listener_addr = _PropertyAddress(CA.kAudioHardwarePropertyDefaultSystemOutputDevice,
                                               CA.kAudioObjectPropertyScopeGlobal,
                                               CA.kAudioObjectPropertyElementMain)
        self._listening = False
        self.format: AudioFormat | None = None

    @property
    def running(self) -> bool:
        return bool(self._agg_id)

    def start(self) -> AudioFormat:
        with self._lock:
            if self._agg_id:
                return self.format
            try:
                self._build()
            except Exception:
                self._teardown()
                raise
            if not self._listening and self._on_device_change is not None:
                _ca.AudioObjectAddPropertyListener(CA.kAudioObjectSystemObject, ctypes.byref(self._listener_addr),
                                                   self._listener, None)
                self._listening = True
            return self.format

    def stop(self) -> None:
        with self._lock:
            if self._listening:
                _ca.AudioObjectRemovePropertyListener(CA.kAudioObjectSystemObject,
                                                      ctypes.byref(self._listener_addr), self._listener, None)
                self._listening = False
            self._teardown()

    def restart(self) -> AudioFormat:
        with self._lock:
            self._teardown()
            self._build()
            return self.format

    def _build(self) -> None:
        own = _own_process_object()
        excluded = NSArray.arrayWithArray_([NSNumber.numberWithUnsignedInt_(own)] if own else [])
        desc = CA.CATapDescription.alloc().initStereoGlobalTapButExcludeProcesses_(excluded)
        desc.setName_("Canard system audio")
        desc.setPrivate_(True)
        status, self._tap_id = CA.AudioHardwareCreateProcessTap(desc, None)
        _check(status, "AudioHardwareCreateProcessTap")

        asbd = _get(self._tap_id, CA.kAudioTapPropertyFormat, 40)
        rate, _fmt, flags, _bpp, _fpp, _bpf, channels, _bits, _r = struct.unpack("dIIIIIIII", asbd)
        self.format = AudioFormat(rate, channels, not flags & _K_FORMAT_FLAG_NON_INTERLEAVED)

        output_uid = _default_output_uid()
        description = _plist({
            _k(CA.kAudioAggregateDeviceNameKey): "Canard System Audio",
            _k(CA.kAudioAggregateDeviceUIDKey): f"canard.tap.{uuid.uuid4()}",
            _k(CA.kAudioAggregateDeviceMainSubDeviceKey): output_uid,
            _k(CA.kAudioAggregateDeviceIsPrivateKey): True,
            _k(CA.kAudioAggregateDeviceIsStackedKey): False,
            _k(CA.kAudioAggregateDeviceTapAutoStartKey): True,
            _k(CA.kAudioAggregateDeviceSubDeviceListKey): [{_k(CA.kAudioSubDeviceUIDKey): output_uid}],
            _k(CA.kAudioAggregateDeviceTapListKey): [
                {_k(CA.kAudioSubTapDriftCompensationKey): True,
                 _k(CA.kAudioSubTapUIDKey): str(desc.UUID().UUIDString())}
            ],
        })
        agg = ctypes.c_uint32()
        _check(_ca.AudioHardwareCreateAggregateDevice(objc.pyobjc_id(description), ctypes.byref(agg)),
               "AudioHardwareCreateAggregateDevice")
        self._agg_id = agg.value
        _check(_ca.AudioDeviceCreateIOProcID(self._agg_id, self._io_proc, None, ctypes.byref(self._proc_id)),
               "AudioDeviceCreateIOProcID")
        _check(_ca.AudioDeviceStart(self._agg_id, self._proc_id), "AudioDeviceStart")

    def _teardown(self) -> None:
        if self._agg_id:
            if self._proc_id:
                _ca.AudioDeviceStop(self._agg_id, self._proc_id)
                _ca.AudioDeviceDestroyIOProcID(self._agg_id, self._proc_id)
                self._proc_id = ctypes.c_void_p()
            CA.AudioHardwareDestroyAggregateDevice(self._agg_id)
            self._agg_id = 0
        if self._tap_id:
            CA.AudioHardwareDestroyProcessTap(self._tap_id)
            self._tap_id = 0

    def _io(self, _dev, _now, in_data, _in_time, _out_data, _out_time, _client) -> int:
        if not in_data:
            return 0
        abl = ctypes.cast(in_data, ctypes.POINTER(_AudioBufferList)).contents
        count = abl.mNumberBuffers
        if count < 1:
            return 0
        buffers = ctypes.cast(ctypes.addressof(abl.mBuffers), ctypes.POINTER(_AudioBuffer * count)).contents
        # Interleaved: tek tampon tüm kanalları taşır. Planar: kanal düzlemleri art arda eklenir (f32-planar).
        data = b"".join(ctypes.string_at(b.mData, b.mDataByteSize) for b in buffers if b.mData and b.mDataByteSize)
        if data:
            self._on_chunk(data)
        return 0

    def _device_changed(self, _obj, _count, _addresses, _client) -> int:
        if self._on_device_change is not None:
            self._on_device_change()
        return 0
