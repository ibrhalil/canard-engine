"""Faz 4 spike — ScreenCaptureKit (macOS 13+) ile sistem sesi yakalama denemesi.

Çalıştır:  uv run python tools/audio-probe/sck_probe.py
Müzik çalarken terminalde seviye çubuğu oynamalı. Ctrl+C ile çık.

SCK ses için bir ekran akışı da ister; görüntü 2×2 piksel ve saniyede 1 kareyle tutulur,
görüntü kareleri işlenmez.
"""

import os
import signal
import struct
import sys
import threading
import time

import objc
import CoreMedia as CM
import dispatch
import ScreenCaptureKit as SCK
from Foundation import NSObject

sys.path.insert(0, os.path.dirname(__file__))
from meter import Meter  # noqa: E402

SCStreamOutput = objc.protocolNamed("SCStreamOutput")
SCStreamDelegate = objc.protocolNamed("SCStreamDelegate")


class AudioOutput(NSObject, protocols=[SCStreamOutput, SCStreamDelegate]):
    def initWithMeter_(self, meter):
        self = objc.super(AudioOutput, self).init()
        if self is None:
            return None
        self.meter = meter
        self.buffers = 0
        self.format_line = None
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if output_type != SCK.SCStreamOutputTypeAudio:
            return
        self.buffers += 1
        if self.format_line is None:
            self.format_line = _describe_format(sample_buffer)
        block = CM.CMSampleBufferGetDataBuffer(sample_buffer)
        if block is None:
            return
        length = CM.CMBlockBufferGetDataLength(block)
        status, data = CM.CMBlockBufferCopyDataBytes(block, 0, length, None)
        if status == 0 and data:
            count = len(data) // 4
            self.meter.feed(struct.unpack(f"{count}f", bytes(data[: count * 4])))

    def stream_didStopWithError_(self, stream, error):
        print(f"\nAkış durdu: {error}")


def _describe_format(sample_buffer) -> str:
    try:
        fmt = CM.CMSampleBufferGetFormatDescription(sample_buffer)
        asbd = CM.CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
        # pyobjc yapıyı doğrudan da, (yapı, ...) demeti olarak da döndürebiliyor.
        if not hasattr(asbd, "mFormatFlags") and isinstance(asbd, (list, tuple)):
            asbd = asbd[0]
        planar = bool(asbd.mFormatFlags & (1 << 5))
        return (f"{asbd.mSampleRate:.0f} Hz, {asbd.mChannelsPerFrame} kanal, {asbd.mBitsPerChannel} bit, "
                f"{'planar' if planar else 'interleaved'}")
    except Exception as error:  # yalnız bilgi amaçlı
        return f"format okunamadı ({error!r})"


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
    content, error = _wait(SCK.SCShareableContent.getShareableContentWithCompletionHandler_)
    if error is not None or content is None:
        print(f"Paylaşılabilir içerik alınamadı: {error}")
        print("→ Sistem Ayarları › Gizlilik ve Güvenlik › Ekran ve Sistem Sesi Kaydı'nda terminal uygulamana izin ver, "
              "terminali yeniden başlat.")
        return 1
    display = content.displays()[0]
    content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])

    config = SCK.SCStreamConfiguration.alloc().init()
    config.setCapturesAudio_(True)
    config.setSampleRate_(48000)
    config.setChannelCount_(2)
    config.setExcludesCurrentProcessAudio_(True)
    config.setWidth_(2)
    config.setHeight_(2)
    config.setMinimumFrameInterval_(CM.CMTimeMake(1, 1))

    meter = Meter()
    output = AudioOutput.alloc().initWithMeter_(meter)
    stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, output)
    queue = dispatch.dispatch_queue_create(b"canard.probe.audio", None)
    ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(output, SCK.SCStreamOutputTypeAudio, queue, None)
    if not ok:
        print(f"Ses çıkışı eklenemedi: {error}")
        return 1

    (error,) = _wait(stream.startCaptureWithCompletionHandler_)
    if error is not None:
        print(f"Yakalama başlatılamadı: {error}")
        return 1
    print("Yakalama başladı — müzik çal. Ctrl+C ile çık.\n")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    shown_format = False
    try:
        while not stop.is_set():
            time.sleep(0.1)
            if not shown_format and output.format_line:
                print(f"Format: {output.format_line}")
                shown_format = True
            meter.render(extra=f"tampon={output.buffers}")
    finally:
        print()
        _wait(stream.stopCaptureWithCompletionHandler_)
        print(meter.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
