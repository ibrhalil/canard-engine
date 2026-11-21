"""Donanım HEVC kodlayıcı (VideoToolbox) — ekran koruyucu kare yayını için.

pyobjc tuzakları (tools/saver-probe ölçümlerinde bulundu):
- VTCompressionSession opak işaretçi döner; özellikler ctypes `VTSessionSetProperty` + CF nesneleriyle verilir
  (pyobjc yolu -12902 döndürür; VT anahtar sabitleri ve NSNumber'lar Python türüne dönüşür),
- VT çıktı geri çağrısı arka plan iş parçacığında çalışır; pyobjc fonksiyonları orada ilk kez aranırsa
  KeyError ile süreç düşer → hepsi modül yüklenirken çözülür; geri çağrıda istisna dışarı sızmaz,
- CVPixelBuffer satırları 64 bayt hizalıdır (ör. 3420 px: 13696 ≠ QImage 13680) → satır satır kopya.
"""

import ctypes
import ctypes.util
import logging
import warnings
from collections.abc import Callable

import objc
import CoreMedia as CM
import Quartz
import VideoToolbox as VT

log = logging.getLogger("canard.saver")
# VTCompressionSession pyobjc'de opak işaretçi; her oturumda basılan uyarı bilinen ve zararsız durum.
warnings.filterwarnings("ignore", category=objc.ObjCPointerWarning)

_cv = ctypes.CDLL(ctypes.util.find_library("CoreVideo"))
_cv.CVPixelBufferGetBaseAddress.restype = ctypes.c_void_p
_cv.CVPixelBufferGetBaseAddress.argtypes = [ctypes.c_void_p]
_cv.CVPixelBufferGetBytesPerRow.restype = ctypes.c_size_t
_cv.CVPixelBufferGetBytesPerRow.argtypes = [ctypes.c_void_p]
_cv.CVPixelBufferLockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
_cv.CVPixelBufferUnlockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
_vt = ctypes.CDLL(ctypes.util.find_library("VideoToolbox"))
_vt.VTSessionSetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_vt.VTSessionSetProperty.restype = ctypes.c_int32
_vt.VTCompressionSessionInvalidate.argtypes = [ctypes.c_void_p]
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_cf.CFNumberCreate.restype = ctypes.c_void_p
_cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
_cf.CFRelease.argtypes = [ctypes.c_void_p]
_CF_TRUE = ctypes.c_void_p.in_dll(_cf, "kCFBooleanTrue").value
_CF_FALSE = ctypes.c_void_p.in_dll(_cf, "kCFBooleanFalse").value
_UTF8 = 0x08000100
_SINT64 = 4

# Geri çağrı iş parçacığında kullanılanlar burada çözülür.
_get_format = CM.CMSampleBufferGetFormatDescription
_get_extension = CM.CMFormatDescriptionGetExtension
_ATOMS = CM.kCMFormatDescriptionExtension_SampleDescriptionExtensionAtoms
_get_data = CM.CMSampleBufferGetDataBuffer
_get_length = CM.CMBlockBufferGetDataLength
_copy_bytes = CM.CMBlockBufferCopyDataBytes
_get_attachments = CM.CMSampleBufferGetSampleAttachmentsArray
_NOT_SYNC = CM.kCMSampleAttachmentKey_NotSync
_encode = VT.VTCompressionSessionEncodeFrameWithOutputHandler
_make_time = CM.CMTimeMake
_get_pts = CM.CMSampleBufferGetPresentationTimeStamp
_MICROS = 1_000_000


def _set(session_ptr: int, key, value) -> int:
    name = _cf.CFStringCreateWithCString(None, str(key).encode(), _UTF8)
    if isinstance(value, bool):
        cf_value, owned = (_CF_TRUE if value else _CF_FALSE), False
    else:
        number = ctypes.c_int64(value)
        cf_value, owned = _cf.CFNumberCreate(None, _SINT64, ctypes.byref(number)), True
    try:
        return _vt.VTSessionSetProperty(session_ptr, name, cf_value)
    finally:
        _cf.CFRelease(name)
        if owned:
            _cf.CFRelease(cf_value)


class HevcEncoder:
    """BGRA QImage'ları donanım HEVC'ye kodlar; her kare için `on_output(keyframe, pts_us, hvcc, data)` çağrılır.

    `on_output` VideoToolbox iş parçacığında çalışır. `pts_us` karenin yakalanma anıdır (mikrosaniye; .saver
    kareleri buna göre eşit aralıkla gösterir). `hvcc` yalnız anahtar karelerde dolu (kod çözücü parametre
    setleri); `data` 4 bayt uzunluk önekli NAL birimleridir.
    """

    def __init__(self, width: int, height: int, fps: int, bitrate: int,
                 on_output: Callable[[bool, bytes | None, bytes], None]):
        self.width, self.height, self.fps = width, height, fps
        self._on_output = on_output
        self._index = 0
        status, self._session = VT.VTCompressionSessionCreate(None, width, height, CM.kCMVideoCodecType_HEVC,
                                                              None, None, None, None, None, None)
        if status != 0 or self._session is None:
            raise OSError(f"VTCompressionSessionCreate başarısız: {status}")
        self._ptr = self._session.pointerAsInteger
        failed = {name: code for name, code in (
            ("RealTime", _set(self._ptr, VT.kVTCompressionPropertyKey_RealTime, True)),
            ("AllowFrameReordering", _set(self._ptr, VT.kVTCompressionPropertyKey_AllowFrameReordering, False)),
            ("AverageBitRate", _set(self._ptr, VT.kVTCompressionPropertyKey_AverageBitRate, bitrate)),
            ("ExpectedFrameRate", _set(self._ptr, VT.kVTCompressionPropertyKey_ExpectedFrameRate, fps)),
            ("MaxKeyFrameInterval", _set(self._ptr, VT.kVTCompressionPropertyKey_MaxKeyFrameInterval, fps * 2)),
        ) if code != 0}
        if failed:
            log.warning("kodlayıcı ayarları uygulanamadı: %s", failed)
        self._handler = self._done

    def encode(self, image, pts_us: int) -> None:
        """image: QImage, Format_ARGB32 (bellekte BGRA), kodlayıcı boyutunda; pts_us: yakalanma anı (µs)."""
        status, pixel_buffer = Quartz.CVPixelBufferCreate(None, self.width, self.height,
                                                          Quartz.kCVPixelFormatType_32BGRA, None, None)
        if status != 0:
            log.warning("CVPixelBufferCreate başarısız: %s", status)
            return
        ptr = objc.pyobjc_id(pixel_buffer)
        _cv.CVPixelBufferLockBaseAddress(ptr, 0)
        try:
            base, row = _cv.CVPixelBufferGetBaseAddress(ptr), _cv.CVPixelBufferGetBytesPerRow(ptr)
            # Yazılabilir görünüm: from_buffer ara kopya yapmadan adresi verir.
            src = ctypes.addressof((ctypes.c_char * image.sizeInBytes()).from_buffer(image.bits()))
            line = image.bytesPerLine()
            if row == line:
                ctypes.memmove(base, src, image.sizeInBytes())
            else:
                width = min(line, row)
                for y in range(self.height):
                    ctypes.memmove(base + y * row, src + y * line, width)
        finally:
            _cv.CVPixelBufferUnlockBaseAddress(ptr, 0)
        _encode(self._session, pixel_buffer, _make_time(pts_us, _MICROS), _make_time(_MICROS // self.fps, _MICROS),
                None, None, self._handler)
        self._index += 1

    def close(self) -> None:
        if self._ptr:
            _vt.VTCompressionSessionInvalidate(self._ptr)
            self._ptr = 0
        self._on_output = lambda *_: None

    def _done(self, status, _flags, sample) -> None:
        # VideoToolbox iş parçacığı: istisna dışarı sızarsa tüm süreç düşer.
        try:
            if status != 0 or sample is None:
                return
            attachments = _get_attachments(sample, False)
            keyframe = not (attachments and attachments[0].get(_NOT_SYNC))
            hvcc = None
            if keyframe:
                atoms = _get_extension(_get_format(sample), _ATOMS)
                if atoms is not None and atoms.get("hvcC") is not None:
                    hvcc = bytes(atoms["hvcC"])
            block = _get_data(sample)
            length = _get_length(block)
            ok, data = _copy_bytes(block, 0, length, None)
            pts = _get_pts(sample)
            pts_us = int(pts.value * _MICROS // pts.timescale) if pts.timescale else 0
            if ok == 0:
                self._on_output(keyframe, pts_us, hvcc, bytes(data))
        except Exception:
            log.exception("kodlayıcı çıktısı işlenemedi")
