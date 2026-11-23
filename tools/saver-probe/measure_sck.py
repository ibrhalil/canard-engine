"""Faz 3a B uygulaması — ScreenCaptureKit ile Canard penceresini GPU'da yakalama + donanım HEVC ölçümü.

measure_capture.py ile aynı kurulum (ekran başına tam ekran duvar kağıdı penceresi, canlı mscillus, sistem
sesi), ama kare kaynağı `grab()` değil: SCK `desktopIndependentWindow` filtresi Canard penceresinin içeriğini
IOSurface olarak verir (örtülü olsa da), CVPixelBuffer kopyalanmadan VideoToolbox'a gider. Ana iş parçacığı
kullanılmaz. Ekran başına her 5 sn: gelen tam kare/sn, boşta (değişmeyen) kare, kodlanan kare/sn, Mbit/sn;
ekran koruyucu/kilit olayları da loglanır → kilitliyken yakalama sürüyor mu görülür.

Çalıştır (müzik çalarken; ölçüm sırasında `sleep 3; open -a ScreenSaverEngine` ile kilit testi):
    uv run python tools/saver-probe/measure_sck.py                 # 60 sn, 60 kare/sn hedef
    uv run python tools/saver-probe/measure_sck.py --seconds 90 --fps 30
Ekran Kaydı izni gerekir (Sistem Ayarları › Gizlilik ve Güvenlik › Ekran ve Sistem Sesi Kaydı › terminal).
"""

import argparse
import resource
import sys
import threading
import time
from pathlib import Path

import objc
import CoreMedia as CM
import dispatch
import Quartz
import ScreenCaptureKit as SCK
import VideoToolbox as VT
from AppKit import NSDistributedNotificationCenter
from Foundation import NSObject
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_capture as base  # noqa: E402  (Encoder, ScreenProbe kurulumu, log)

log = base.log
# VideoToolbox/SCK iş parçacıklarında tembel yükleme KeyError'ı yaşanmasın diye ana iş parçacığında çözülür.
_get_image_buffer = CM.CMSampleBufferGetImageBuffer
_get_pts = CM.CMSampleBufferGetPresentationTimeStamp
_get_attachments = CM.CMSampleBufferGetSampleAttachmentsArray
_encode = VT.VTCompressionSessionEncodeFrameWithOutputHandler
_TIME_INVALID = CM.kCMTimeInvalid
_STATUS_KEY = SCK.SCStreamFrameInfoStatus
_COMPLETE = SCK.SCFrameStatusComplete
_IDLE = SCK.SCFrameStatusIdle
_SCREEN = SCK.SCStreamOutputTypeScreen

SCStreamOutput = objc.protocolNamed("SCStreamOutput")
SCStreamDelegate = objc.protocolNamed("SCStreamDelegate")


class FrameSink(NSObject, protocols=[SCStreamOutput, SCStreamDelegate]):
    def initWithEncoder_name_(self, encoder, name):
        self = objc.super(FrameSink, self).init()
        if self is None:
            return None
        self.encoder = encoder
        self.name = name
        self.lock = threading.Lock()
        self.complete = 0
        self.idle = 0
        self.other = 0
        self.callback_errors = 0
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, output_type):
        if output_type != _SCREEN:
            return
        try:
            attachments = _get_attachments(sample, False)
            status = attachments[0].get(_STATUS_KEY) if attachments else None
            if status == _COMPLETE:
                pixel_buffer = _get_image_buffer(sample)
                if pixel_buffer is not None:
                    _encode(self.encoder.session, pixel_buffer, _get_pts(sample), _TIME_INVALID,
                            None, None, self.encoder._done)
                with self.lock:
                    self.complete += 1
            elif status == _IDLE:
                with self.lock:
                    self.idle += 1
            else:
                with self.lock:
                    self.other += 1
        except Exception:
            with self.lock:
                self.callback_errors += 1

    def stream_didStopWithError_(self, stream, error):
        log(f"{self.name}: SCK akışı durdu: {error}")

    def take(self):
        with self.lock:
            result = (self.complete, self.idle, self.other, self.callback_errors)
            self.complete = self.idle = self.other = self.callback_errors = 0
        return result


_ALWAYS_VISIBLE = {"on": False}


def keep_exposed(ns_window) -> None:
    """Qt örtülü/kilitli pencerede çizimi durdurur (windowDidChangeOcclusionState → isExposed=false).
    Yayın sürerken Canard'ın kendi pencere sınıfında occlusionState "görünür" döner; SCK yeni kare alır."""
    cls = type(ns_window)
    if getattr(cls, "_canard_keep_exposed", False):
        return
    from AppKit import NSWindowOcclusionStateVisible

    def occlusionState(self):
        real = objc.super(cls, self).occlusionState()
        return real | NSWindowOcclusionStateVisible if _ALWAYS_VISIBLE["on"] else real

    objc.classAddMethods(cls, [objc.selector(occlusionState, selector=b"occlusionState", signature=b"Q@:")])
    cls._canard_keep_exposed = True
    log(f"occlusionState geçersiz kılındı: {cls.__name__}")


class _Events(NSObject):
    def fire_(self, note):
        log(f"[olay] {note.name()}")


def _wait(fn, timeout=10.0):
    done = threading.Event()
    result = {}

    def handler(*args):
        result["args"] = args
        done.set()

    fn(handler)
    if not done.wait(timeout):
        raise TimeoutError("ScreenCaptureKit yanıt vermedi")
    return result["args"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://mscillus.ibrhalil.com/")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--bitrate", type=float, default=20, help="Mbit/sn (ekran başına)")
    parser.add_argument("--no-boot", action="store_true")
    parser.add_argument("--warmup", type=int, default=6)
    parser.add_argument("--keep-exposed", action="store_true",
                        help="ölçüm boyunca pencereler örtülü/kilitliyken de çizilsin (occlusionState geçersiz kılma)")
    args = parser.parse_args()

    app = QApplication(sys.argv[:1])
    adapter = base.detect()
    profile = base.create_profile(persistent=False)
    base.install_bridge(profile)
    hub = base.SystemAudioHub(adapter, app)
    base.system_audio.install(profile)
    boot = None if args.no_boot else (base.ROOT / "tools" / "mscillus-boot-system.js").read_text(encoding="utf-8")
    probes = [base.ScreenProbe(s, profile, args.url, boot, hub, adapter, args.fps, int(args.bitrate * 1e6))
              for s in QGuiApplication.screens()]

    events = _Events.alloc().init()
    center = NSDistributedNotificationCenter.defaultCenter()
    for name in ("com.apple.screensaver.didstart", "com.apple.screensaver.didstop",
                 "com.apple.screenIsLocked", "com.apple.screenIsUnlocked"):
        center.addObserver_selector_name_object_(events, "fire:", name, None)

    streams = []
    queue = dispatch.dispatch_queue_create(b"canard.sck.frames", None)
    state = {"since": None, "start": None, "rusage": None, "totals": {}}

    def start_capture():
        content, error = _wait(lambda h: SCK.SCShareableContent
                               .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                                   False, False, h))
        if content is None:
            log(f"paylaşılabilir içerik alınamadı (Ekran Kaydı izni?): {error}")
            app.quit()
            return
        windows = {w.windowID(): w for w in content.windows()}
        if args.keep_exposed:
            for probe in probes:
                keep_exposed(objc.objc_object(c_void_p=int(probe.window.winId())).window())
            _ALWAYS_VISIBLE["on"] = True
        for probe in probes:
            number = objc.objc_object(c_void_p=int(probe.window.winId())).window().windowNumber()
            sc_window = windows.get(number)
            if sc_window is None:
                log(f"{probe.name}: pencere SCK listesinde yok (windowNumber={number})")
                continue
            content_filter = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(sc_window)
            config = SCK.SCStreamConfiguration.alloc().init()
            config.setWidth_(probe.width)
            config.setHeight_(probe.height)
            config.setPixelFormat_(Quartz.kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)  # kodlayıcının yerel biçimi
            config.setMinimumFrameInterval_(CM.CMTimeMake(1, args.fps))
            config.setQueueDepth_(5)
            config.setShowsCursor_(False)
            config.setIgnoreShadowsSingleWindow_(True)
            sink = FrameSink.alloc().initWithEncoder_name_(probe.encoder, probe.name)
            stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, sink)
            ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(sink, _SCREEN, queue, None)
            if not ok:
                log(f"{probe.name}: çıkış eklenemedi: {error}")
                continue
            (error,) = _wait(stream.startCaptureWithCompletionHandler_)
            if error is not None:
                log(f"{probe.name}: yakalama başlatılamadı: {error}")
                continue
            streams.append((probe, stream, sink))
            state["totals"][probe.name] = [0, 0, 0]
            log(f"{probe.name}: SCK pencere yakalama başladı {probe.width}x{probe.height} @ {args.fps}, "
                f"donanım HEVC={probe.encoder.hardware}")
        state["since"] = state["start"] = time.monotonic()
        state["rusage"] = resource.getrusage(resource.RUSAGE_SELF)
        reporter.start(5000)
        QTimer.singleShot(args.seconds * 1000, finish)

    def report():
        now = time.monotonic()
        elapsed = now - state["since"]
        state["since"] = now
        for probe, _stream, sink in streams:
            complete, idle, other, errors = sink.take()
            out_frames, out_bytes, enc_errors = probe.encoder.take()
            totals = state["totals"][probe.name]
            totals[0] += out_frames
            totals[1] += out_bytes
            totals[2] += complete
            handle = probe.window.windowHandle()
            exposed = "görünür" if handle is not None and handle.isExposed() else "GİZLİ"
            log(f"{probe.name:24} Qt:{exposed} · gelen {complete / elapsed:4.1f} tam kare/sn (boşta {idle / elapsed:4.1f}, diğer "
                f"{other}) · kodlanan {out_frames / elapsed:4.1f}/sn · {out_bytes * 8 / elapsed / 1e6:5.1f} Mbit/sn"
                + (f" · geri çağrı hatası {errors}" if errors else "") + (f" · kodlama hatası {enc_errors}" if enc_errors else ""))

    reporter = QTimer()
    reporter.timeout.connect(report)

    def finish():
        if state.get("finished"):
            return
        state["finished"] = True
        reporter.stop()
        report()
        wall = time.monotonic() - state["start"]
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (usage.ru_utime - state["rusage"].ru_utime) + (usage.ru_stime - state["rusage"].ru_stime)
        log("=" * 30 + " ÖZET " + "=" * 30)
        for probe, stream, _sink in streams:
            out_frames, out_bytes, complete = state["totals"][probe.name]
            log(f"{probe.name}: {probe.width}x{probe.height} · ortalama {complete / wall:.1f} gelen / "
                f"{out_frames / wall:.1f} kodlanan kare/sn · {out_bytes * 8 / wall / 1e6:.1f} Mbit/sn")
            _wait(stream.stopCaptureWithCompletionHandler_)
        log(f"süreç işlemci kullanımı: %{cpu / wall * 100:.0f} (grab ölçümünde %193 idi)")
        hub.shutdown()
        app.quit()

    import signal

    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, finish if state["start"] else app.quit))
    keepalive = QTimer()
    keepalive.timeout.connect(lambda: None)
    keepalive.start(250)
    QTimer.singleShot(args.warmup * 1000, start_capture)
    log(f"içerik: {args.url} · {args.warmup} sn ısınma, sonra {args.seconds} sn SCK ölçümü @ {args.fps} kare/sn")
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())
