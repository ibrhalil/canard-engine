"""Faz 3a B uygulaması — adım 0: gerçek içerikle tam ekran kare yakalama + donanım HEVC kodlama ölçümü.

Her ekranda tam ekran duvar kağıdı penceresinde içeriği açar (varsayılan canlı mscillus + sistem sesi boot
script'i), hedef kare hızında `grab()` → BGRA → CVPixelBuffer → VideoToolbox HEVC kodlar ve ekran başına
ölçer: grab / görüntüye çevirme / kopyalama / kodlamaya gönderme süreleri, ulaşılan kare/sn, gerçek bit hızı;
süreç işlemci kullanımı. Geçici (kalıcı olmayan) profil kullanır: çalışan Canard'la çakışmaz.

Çalıştır (müzik çalarken):
    uv run python tools/saver-probe/measure_capture.py                      # 30 sn, 30 kare/sn
    uv run python tools/saver-probe/measure_capture.py --fps 60 --seconds 20
    uv run python tools/saver-probe/measure_capture.py --url https://… --no-boot
"""

import argparse
import ctypes
import ctypes.util
import resource
import statistics
import sys
import threading
import time
from pathlib import Path

import objc
import CoreMedia as CM
import Quartz
import VideoToolbox as VT
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPalette
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from canard.audio.hub import SystemAudioHub  # noqa: E402
from canard.platform.base import detect  # noqa: E402
from canard.web.bridge import HostBridge  # noqa: E402
from canard.web.chromium import scripts, system_audio  # noqa: E402
from canard.web.chromium.engine import BRIDGE_SCRIPT, create_profile  # noqa: E402
from canard.web.chromium.engine import ChromiumHost as WebHost  # noqa: E402


def install_bridge(profile) -> None:
    scripts.install(profile, BRIDGE_SCRIPT, HostBridge("wallpaper", None, True, "chromium").script_source())

_cv = ctypes.CDLL(ctypes.util.find_library("CoreVideo"))
_cv.CVPixelBufferGetBaseAddress.restype = ctypes.c_void_p
_cv.CVPixelBufferGetBaseAddress.argtypes = [ctypes.c_void_p]
_cv.CVPixelBufferGetBytesPerRow.restype = ctypes.c_size_t
_cv.CVPixelBufferGetBytesPerRow.argtypes = [ctypes.c_void_p]
_cv.CVPixelBufferLockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
_cv.CVPixelBufferUnlockBaseAddress.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
_vt = ctypes.CDLL(ctypes.util.find_library("VideoToolbox"))
# pyobjc oturumu opak işaretçi olarak döndürüyor; özellik ayarları ctypes ile (aksi halde -12902).
_vt.VTSessionSetProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_vt.VTSessionSetProperty.restype = ctypes.c_int32
_vt.VTSessionCopyProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
_vt.VTSessionCopyProperty.restype = ctypes.c_int32

_T0 = time.monotonic()
# pyobjc fonksiyonları ilk kullanımda tembel yükler; VideoToolbox'ın arka plan iş parçacığında ilk arama
# KeyError verip süreci düşürdü — burada, ana iş parçacığında çözülür.
_sample_size = CM.CMSampleBufferGetTotalSampleSize
_cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
_cf.CFStringCreateWithCString.restype = ctypes.c_void_p
_cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
_cf.CFNumberCreate.restype = ctypes.c_void_p
_cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
_CF_TRUE = ctypes.c_void_p.in_dll(_cf, "kCFBooleanTrue").value
_CF_FALSE = ctypes.c_void_p.in_dll(_cf, "kCFBooleanFalse").value


def _cfstr(key) -> int:
    # pyobjc VT anahtar sabitlerini ve NSNumber'ları Python türlerine çeviriyor; CF nesneleri doğrudan üretilir.
    return _cf.CFStringCreateWithCString(None, str(key).encode(), 0x08000100)  # kCFStringEncodingUTF8


def _cfvalue(value) -> int:
    if isinstance(value, bool):
        return _CF_TRUE if value else _CF_FALSE
    number = ctypes.c_int64(value)
    return _cf.CFNumberCreate(None, 4, ctypes.byref(number))  # kCFNumberSInt64Type


def log(message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} +{time.monotonic() - _T0:6.1f}s  {message}", flush=True)


class Encoder:
    def __init__(self, width: int, height: int, fps: int, bitrate: int):
        self.width, self.height = width, height
        status, self.session = VT.VTCompressionSessionCreate(None, width, height, CM.kCMVideoCodecType_HEVC,
                                                             None, None, None, None, None, None)
        if status != 0:
            raise OSError(f"VTCompressionSessionCreate {status}")
        self._ptr = self.session.pointerAsInteger
        settings = {
            VT.kVTCompressionPropertyKey_RealTime: True,
            VT.kVTCompressionPropertyKey_AllowFrameReordering: False,
            VT.kVTCompressionPropertyKey_AverageBitRate: bitrate,
            VT.kVTCompressionPropertyKey_ExpectedFrameRate: fps,
            VT.kVTCompressionPropertyKey_MaxKeyFrameInterval: fps * 2,
        }
        self.settings_status = {str(k).replace("kVTCompressionPropertyKey_", ""):
                                _vt.VTSessionSetProperty(self._ptr, _cfstr(k), _cfvalue(v))
                                for k, v in settings.items()}
        value = ctypes.c_void_p()
        hw_key = getattr(VT, "kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder", None)
        self.hardware = None
        if hw_key is not None and _vt.VTSessionCopyProperty(self._ptr, _cfstr(hw_key), None,
                                                            ctypes.byref(value)) == 0 and value.value:
            self.hardware = bool(objc.objc_object(c_void_p=value.value))
        self._lock = threading.Lock()
        self.out_bytes = 0
        self.out_frames = 0
        self.errors = 0
        self.index = 0
        self.fps = fps

    def _done(self, status, _flags, sample):
        # VideoToolbox iş parçacığında çalışır; buradaki bir Python hatası tüm süreci sonlandırır.
        try:
            size = _sample_size(sample) if status == 0 and sample is not None else None
        except Exception:
            size = None
        with self._lock:
            if size is None:
                self.errors += 1
            else:
                self.out_frames += 1
                self.out_bytes += size

    def encode(self, image: QImage) -> tuple[float, float]:
        started = time.perf_counter()
        status, pixel_buffer = Quartz.CVPixelBufferCreate(None, self.width, self.height,
                                                          Quartz.kCVPixelFormatType_32BGRA, None, None)
        ptr = objc.pyobjc_id(pixel_buffer)
        _cv.CVPixelBufferLockBaseAddress(ptr, 0)
        base, row = _cv.CVPixelBufferGetBaseAddress(ptr), _cv.CVPixelBufferGetBytesPerRow(ptr)
        # Yazılabilir görünüm: from_buffer ara kopya yapmadan adresi verir.
        src = ctypes.addressof((ctypes.c_char * image.sizeInBytes()).from_buffer(image.bits()))
        line = image.bytesPerLine()
        if row == line:
            ctypes.memmove(base, src, image.sizeInBytes())
        else:
            # CVPixelBuffer satırları 64 bayta hizalı (ör. 3420 px: 13696 ≠ 13680): satır satır, kopyasız kaynak.
            width = min(line, row)
            for y in range(self.height):
                ctypes.memmove(base + y * row, src + y * line, width)
        _cv.CVPixelBufferUnlockBaseAddress(ptr, 0)
        copied = time.perf_counter()
        VT.VTCompressionSessionEncodeFrameWithOutputHandler(
            self.session, pixel_buffer, CM.CMTimeMake(self.index, self.fps), CM.CMTimeMake(1, self.fps),
            None, None, self._done)
        self.index += 1
        return (copied - started) * 1000, (time.perf_counter() - copied) * 1000

    def take(self) -> tuple[int, int, int]:
        with self._lock:
            result = (self.out_frames, self.out_bytes, self.errors)
            self.out_frames = self.out_bytes = self.errors = 0
        return result


class ScreenProbe:
    def __init__(self, screen, profile, url, boot, hub, adapter, fps, bitrate):
        self.name = screen.name()
        self.window = QWidget(None, Qt.WindowType.FramelessWindowHint)
        self.window.setGeometry(screen.geometry())
        palette = self.window.palette()
        palette.setColor(QPalette.ColorRole.Window, Qt.GlobalColor.black)
        self.window.setPalette(palette)
        self.host = WebHost(profile, boot_script=boot, audio_hub=hub)
        layout = QVBoxLayout(self.window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.host)
        self.host.load(url)
        self.window.show()
        adapter.apply_wallpaper_window(self.window)
        g = screen.geometry()
        dpr = screen.devicePixelRatio()
        self.width, self.height = int(g.width() * dpr), int(g.height() * dpr)
        self.encoder = Encoder(self.width, self.height, fps, bitrate)
        self.samples = {"grab": [], "image": [], "copy": [], "submit": []}
        self.all = {k: [] for k in self.samples}
        self.frames = 0
        self.total_frames = 0
        self.total_bytes = 0

    def capture(self) -> None:
        t0 = time.perf_counter()
        pixmap = self.host.view.grab()
        t1 = time.perf_counter()
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)  # bellekte BGRA
        t2 = time.perf_counter()
        if image.width() != self.width or image.height() != self.height:
            image = image.scaled(self.width, self.height)
        copy_ms, submit_ms = self.encoder.encode(image)
        for key, value in (("grab", (t1 - t0) * 1000), ("image", (t2 - t1) * 1000), ("copy", copy_ms),
                           ("submit", submit_ms)):
            self.samples[key].append(value)
        self.frames += 1

    def report(self, elapsed: float) -> None:
        out_frames, out_bytes, errors = self.encoder.take()
        self.total_frames += out_frames
        self.total_bytes += out_bytes
        med = {k: statistics.median(v) if v else 0.0 for k, v in self.samples.items()}
        main_thread = sum(med.values())
        log(f"{self.name:24} {self.width}x{self.height} · yakalanan {self.frames / elapsed:4.1f} kare/sn · "
            f"kodlanan {out_frames / elapsed:4.1f}/sn · {out_bytes * 8 / elapsed / 1e6:5.1f} Mbit/sn · "
            f"grab {med['grab']:.1f} + görüntü {med['image']:.1f} + kopya {med['copy']:.1f} + gönder "
            f"{med['submit']:.1f} = {main_thread:.1f} ms/kare" + (f" · HATA {errors}" if errors else ""))
        for key, values in self.samples.items():
            self.all[key].extend(values)
            values.clear()
        self.frames = 0


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))] if ordered else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://mscillus.ibrhalil.com/")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--bitrate", type=float, default=20, help="Mbit/sn (ekran başına)")
    parser.add_argument("--no-boot", action="store_true", help="mscillus sistem sesi boot script'ini çalıştırma")
    parser.add_argument("--warmup", type=int, default=6, help="ölçüm öncesi bekleme (sn)")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    adapter = detect()
    profile = create_profile(persistent=False)
    install_bridge(profile)
    hub = SystemAudioHub(adapter, app)
    system_audio.install(profile)
    boot = None if args.no_boot else (ROOT / "tools" / "mscillus-boot-system.js").read_text(encoding="utf-8")

    probes = [ScreenProbe(s, profile, args.url, boot, hub, adapter, args.fps, int(args.bitrate * 1e6))
              for s in QGuiApplication.screens()]
    for p in probes:
        log(f"{p.name}: kodlayıcı {p.width}x{p.height}, donanım={p.encoder.hardware}, ayarlar={p.encoder.settings_status}")
    log(f"içerik: {args.url} · {args.warmup} sn ısınma, sonra {args.seconds} sn ölçüm @ {args.fps} kare/sn")

    state = {"since": None, "rusage": None, "wall": None, "end": None}

    def tick():
        for p in probes:
            p.capture()

    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.timeout.connect(tick)

    def periodic():
        now = time.monotonic()
        for p in probes:
            p.report(now - state["since"])
        state["since"] = now
        if now >= state["end"]:
            finish()

    reporter = QTimer()
    reporter.timeout.connect(periodic)

    def begin():
        state["since"] = state["wall"] = time.monotonic()
        state["end"] = state["wall"] + args.seconds
        state["rusage"] = resource.getrusage(resource.RUSAGE_SELF)
        timer.start(max(1, round(1000 / args.fps)))
        reporter.start(5000)

    def finish():
        timer.stop()
        reporter.stop()
        wall = time.monotonic() - state["wall"]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (usage.ru_utime - state["rusage"].ru_utime) + (usage.ru_stime - state["rusage"].ru_stime)
        log("=" * 30 + " ÖZET " + "=" * 30)
        for p in probes:
            total = {k: percentile(v, 0.5) for k, v in p.all.items()}
            p95 = sum(percentile(v, 0.95) for v in p.all.values())
            log(f"{p.name}: {p.width}x{p.height} · ortalama {p.total_frames / wall:.1f} kodlanan kare/sn · "
                f"{p.total_bytes * 8 / wall / 1e6:.1f} Mbit/sn · ana iş parçacığı medyan {sum(total.values()):.1f} ms, "
                f"p95 {p95:.1f} ms/kare (grab {total['grab']:.1f}, görüntü {total['image']:.1f}, "
                f"kopya {total['copy']:.1f}, gönder {total['submit']:.1f})")
        log(f"süreç işlemci kullanımı: %{cpu / wall * 100:.0f} (Python süreci; Chromium/GPU süreçleri hariç)")
        hub.shutdown()
        app.quit()

    QTimer.singleShot(args.warmup * 1000, begin)
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
