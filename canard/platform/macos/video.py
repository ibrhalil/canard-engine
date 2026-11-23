"""Donanım HEVC kodlayıcı (VideoToolbox), ekran koruyucu kare yayını için.

pyobjc tuzakları:
- özellikler ctypes `VTSessionSetProperty` + CF nesneleriyle verilir (pyobjc yolu -12902 döndürüyor),
- çıktı geri çağrısı arka plan iş parçacığında: pyobjc işlevleri modül yüklenirken çözülür, istisna sızmaz,
- CVPixelBuffer satırları 64 bayt hizalı olabilir → gerekirse satır satır kopya.
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
# VTCompressionSession pyobjc'de opak işaretçi; uyarı zararsız.
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
_cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))


class _CGRect(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double), ("width", ctypes.c_double),
                ("height", ctypes.c_double)]


_cg.CGBitmapContextCreate.restype = ctypes.c_void_p
_cg.CGBitmapContextCreate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                                      ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint32]
_cg.CGContextSetBlendMode.argtypes = [ctypes.c_void_p, ctypes.c_int32]
_cg.CGContextDrawImage.argtypes = [ctypes.c_void_p, _CGRect, ctypes.c_void_p]
_cg.CGContextRelease.argtypes = [ctypes.c_void_p]
_BGRA_PREMULTIPLIED = Quartz.kCGImageAlphaPremultipliedFirst | Quartz.kCGBitmapByteOrder32Little
_BLEND_COPY = 17  # kCGBlendModeCopy: saydam pikseller tampondaki eskiyle karışmasın
_SRGB = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
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
_FORCE_KEYFRAME = {VT.kVTEncodeFrameOptionKey_ForceKeyFrame: True}
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
    """QImage (BGRA) ya da CGImage karelerini donanım HEVC'ye kodlar; her kare için
    `on_output(keyframe, pts_us, hvcc, data)` çağrılır.

    `on_output` VideoToolbox iş parçacığında çalışır. `pts_us` karenin yakalanma anıdır (mikrosaniye; .saver
    kareleri buna göre eşit aralıkla gösterir). `hvcc` yalnız anahtar karelerde dolu (kod çözücü parametre
    setleri); `data` 4 bayt uzunluk önekli NAL birimleridir.
    """

    def __init__(self, width: int, height: int, fps: int, bitrate: int,
                 on_output: Callable[[bool, int, bytes | None, bytes], None]):
        self.width, self.height, self.fps = width, height, fps
        self._on_output = on_output
        status, self._session = VT.VTCompressionSessionCreate(None, width, height, CM.kCMVideoCodecType_HEVC,
                                                              None, None, None, None, None, None)
        if status != 0 or self._session is None:
            raise OSError(f"VTCompressionSessionCreate failed: {status}")
        self._ptr = self._session.pointerAsInteger
        failed = {name: code for name, code in (
            ("RealTime", _set(self._ptr, VT.kVTCompressionPropertyKey_RealTime, True)),
            ("AllowFrameReordering", _set(self._ptr, VT.kVTCompressionPropertyKey_AllowFrameReordering, False)),
            ("AverageBitRate", _set(self._ptr, VT.kVTCompressionPropertyKey_AverageBitRate, bitrate)),
            ("ExpectedFrameRate", _set(self._ptr, VT.kVTCompressionPropertyKey_ExpectedFrameRate, fps)),
            ("MaxKeyFrameInterval", _set(self._ptr, VT.kVTCompressionPropertyKey_MaxKeyFrameInterval, fps * 2)),
        ) if code != 0}
        if failed:
            log.warning("encoder properties not applied: %s", failed)
        self._handler = self._done

    def encode(self, image, pts_us: int, force_keyframe: bool = False) -> None:
        """image: Format_ARGB32 QImage (bellekte BGRA, kodlayıcı boyutunda; Chromium) ya da CGImage (herhangi bir
        boyutta, kodlayıcı boyutuna ölçeklenir; WebKit). pts_us: yakalanma anı (µs)."""
        with objc.autorelease_pool():  # Canard'ın kodlama iş parçacığında çağrılır; orada otomatik havuz yok
            self._encode_now(image, pts_us, force_keyframe)

    def _encode_now(self, image, pts_us: int, force_keyframe: bool) -> None:
        status, pixel_buffer = Quartz.CVPixelBufferCreate(None, self.width, self.height,
                                                          Quartz.kCVPixelFormatType_32BGRA, None, None)
        if status != 0:
            log.warning("CVPixelBufferCreate failed: %s", status)
            return
        ptr = objc.pyobjc_id(pixel_buffer)
        _cv.CVPixelBufferLockBaseAddress(ptr, 0)
        try:
            base, row = _cv.CVPixelBufferGetBaseAddress(ptr), _cv.CVPixelBufferGetBytesPerRow(ptr)
            if hasattr(image, "bits"):
                self._copy_qimage(image, base, row)
            else:
                self._draw_cgimage(image, base, row)
        finally:
            _cv.CVPixelBufferUnlockBaseAddress(ptr, 0)
        _encode(self._session, pixel_buffer, _make_time(pts_us, _MICROS), _make_time(_MICROS // self.fps, _MICROS),
                _FORCE_KEYFRAME if force_keyframe else None, None, self._handler)

    def _copy_qimage(self, image, base: int, row: int) -> None:
        src = ctypes.addressof((ctypes.c_char * image.sizeInBytes()).from_buffer(image.bits()))
        line = image.bytesPerLine()
        if row == line:
            ctypes.memmove(base, src, image.sizeInBytes())
        else:
            width = min(line, row)
            for y in range(self.height):
                ctypes.memmove(base + y * row, src + y * line, width)

    def _draw_cgimage(self, image, base: int, row: int) -> None:
        # Görüntünün kendi renk uzayıyla çizilir: renk dönüşümü yok (Chromium yolu da baytları olduğu gibi kopyalar).
        space = Quartz.CGImageGetColorSpace(image) or _SRGB
        context = _cg.CGBitmapContextCreate(base, self.width, self.height, 8, row, objc.pyobjc_id(space),
                                            _BGRA_PREMULTIPLIED)
        if not context:  # desteklenmeyen renk uzayı
            context = _cg.CGBitmapContextCreate(base, self.width, self.height, 8, row, objc.pyobjc_id(_SRGB),
                                                _BGRA_PREMULTIPLIED)
        if not context:
            log.warning("CGBitmapContextCreate failed")
            return
        try:
            _cg.CGContextSetBlendMode(context, _BLEND_COPY)
            _cg.CGContextDrawImage(context, _CGRect(0, 0, self.width, self.height), objc.pyobjc_id(image))
        finally:
            _cg.CGContextRelease(context)

    def close(self) -> None:
        if self._ptr:
            _vt.VTCompressionSessionInvalidate(self._ptr)
            self._ptr = 0
        self._on_output = lambda *_: None

    def _done(self, status, _flags, sample) -> None:
        # İstisna VideoToolbox iş parçacığından sızarsa süreç düşer.
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
            log.exception("encoder output could not be processed")
