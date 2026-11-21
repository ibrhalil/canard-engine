"""Deneme betikleri için ortak seviye göstergesi (terminal çubuğu)."""

import math
import sys
import threading


class Meter:
    def __init__(self):
        self._lock = threading.Lock()
        self._peak = 0.0
        self._sum_sq = 0.0
        self._count = 0
        self._samples_total = 0
        self._nonzero_total = 0
        self._max_peak = 0.0

    def feed(self, samples) -> None:
        peak = 0.0
        sum_sq = 0.0
        nonzero = 0
        for value in samples:
            if value:
                nonzero += 1
                magnitude = abs(value)
                if magnitude > peak:
                    peak = magnitude
                sum_sq += value * value
        with self._lock:
            self._peak = max(self._peak, peak)
            self._sum_sq += sum_sq
            self._count += len(samples)
            self._samples_total += len(samples)
            self._nonzero_total += nonzero
            self._max_peak = max(self._max_peak, peak)

    def render(self, extra: str = "") -> None:
        with self._lock:
            peak, sum_sq, count = self._peak, self._sum_sq, self._count
            self._peak, self._sum_sq, self._count = 0.0, 0.0, 0
        rms = math.sqrt(sum_sq / count) if count else 0.0
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        width = 40
        filled = max(0, min(width, int((db + 60) / 60 * width)))
        bar = "█" * filled + "·" * (width - filled)
        sys.stdout.write(f"\r{bar} {db:6.1f} dBFS  tepe={peak:.3f}  {extra}   ")
        sys.stdout.flush()

    def summary(self) -> str:
        ratio = (self._nonzero_total / self._samples_total * 100) if self._samples_total else 0.0
        verdict = "SES GELDİ ✓" if self._max_peak > 0.001 else "SES YOK ✗ (hep sıfır — izin/aygıt sorunu olabilir)"
        return (f"Özet: {self._samples_total} örnek, sıfır olmayan %{ratio:.1f}, "
                f"en yüksek tepe {self._max_peak:.3f} → {verdict}")
