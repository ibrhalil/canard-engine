"""Motor kıyası: aynı içeriği QtWebEngine (bugünkü Canard) ve Swift + WKWebView ile aynı koşulda ölçer.

Her çalıştırma içeriği ana ekranı kaplayan pencerede açar, ısınma süresinden sonra ölçer, kapatır ve sonucu
build/engine-bench/results.jsonl dosyasına ekler. Ölçülenler:
  - kare hızı: sayfanın requestAnimationFrame hızı (ort., en düşük %5, en uzun kare)
  - CPU: motorun tüm süreçlerinin (Qt: alt süreçler; WebKit: uygulamaya bağlı XPC süreçleri) harcadığı çekirdek
  - bellek: aynı süreçlerin toplam RSS'i (ort., tepe)
  - GPU: sistem geneli "Device Utilization %" (Canard kapalı olmalı; `idle` ile taban ölçülür)
  - --capture: Canard.saver hattı (kare yakala → BGRA, --encode ile donanım HEVC) kare başına süre

Örnekler (proje klasöründe):
  tools/engine-bench/build.sh                                        # WebKit tarafını bir kez derle
  uv run python tools/engine-bench/run.py idle                       # taban (GPU/CPU boşta)
  uv run python tools/engine-bench/run.py qt --label mod3 --keys 3
  uv run python tools/engine-bench/run.py webkit --label mod3 --keys 3
  uv run python tools/engine-bench/run.py qt --label mod3-yayın --keys 3 --capture 30 --encode
  uv run python tools/engine-bench/run.py compare                    # tüm sonuçlar tablo hâlinde

Isınma süresinde (varsayılan 15 sn) pencereye tıklayıp içerikte ayar yapılabilir; ayarlar motor başına kalıcıdır.
Esc ölçümü erken bitirir.
"""

import argparse
import ctypes
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "build" / "engine-bench"
RESULTS = OUT / "results.jsonl"
WEBKIT_APP = OUT / "WebKitBench.app" / "Contents" / "MacOS" / "WebKitBench"
DEFAULT_URL = "https://mscillus.ibrhalil.com/"
BOOTS = {"mic": ROOT / "tools" / "mscillus-boot-mic.js", "system": ROOT / "tools" / "mscillus-boot-system.js"}

_libc = ctypes.CDLL(None)
_responsible = _libc.responsibility_get_pid_responsible_for_pid
_responsible.argtypes = [ctypes.c_int]
_responsible.restype = ctypes.c_int


def _cpu_seconds(text: str) -> float:
    days = 0
    if "-" in text:
        day, text = text.split("-", 1)
        days = int(day)
    parts = [float(p) for p in text.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return days * 86400 + seconds


def _processes() -> dict[int, tuple[int, float, int, str]]:
    """pid → (ppid, CPU sn, RSS KB, komut)."""
    output = subprocess.run(["ps", "-axo", "pid=,ppid=,time=,rss=,comm="], capture_output=True, text=True).stdout
    table = {}
    for line in output.splitlines():
        fields = line.split(None, 4)
        if len(fields) == 5:
            table[int(fields[0])] = (int(fields[1]), _cpu_seconds(fields[2]), int(fields[3]), fields[4])
    return table


def _engine_pids(root: int, table: dict) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, (ppid, *_rest) in table.items():
        children.setdefault(ppid, []).append(pid)
    found, stack = set(), [root]
    while stack:
        pid = stack.pop()
        if pid in table and pid not in found:
            found.add(pid)
            stack.extend(children.get(pid, []))
    # WebKit süreçleri (WebContent, GPU, Networking) launchd'nin çocuğu; sorumlu süreç olarak uygulamaya bağlılar.
    for pid in table:
        if pid not in found and _responsible(pid) == root:
            found.add(pid)
    return found


def _gpu_utilization() -> int | None:
    output = subprocess.run(["/usr/sbin/ioreg", "-r", "-d", "1", "-c", "IOAccelerator"],
                            capture_output=True, text=True).stdout
    match = re.search(r'"Device Utilization %"=(\d+)', output)
    return int(match.group(1)) if match else None


def _canard_running() -> bool:
    return subprocess.run(["pgrep", "-f", "canard.app.main"], capture_output=True).returncode == 0


def _command(args) -> list[str]:
    if args.engine == "qt":
        command = [sys.executable, str(HERE / "qt_bench.py")]
        if args.boot == "system":
            command.append("--system-audio")
    else:
        if not WEBKIT_APP.exists():
            sys.exit("WebKit tarafı derlenmemiş: tools/engine-bench/build.sh")
        if args.boot == "system":
            sys.exit("WebKit tarafında sistem sesi köprüsü yok; adil kıyas için --boot mic (varsayılan)")
        command = [str(WEBKIT_APP)]
    command += ["--url", args.url, "--keys", str(args.keys)]
    boot = BOOTS.get(args.boot, Path(args.boot) if args.boot != "none" else None)
    if boot is not None:
        command += ["--boot", str(boot)]
    if args.capture:
        command += ["--capture", str(args.capture)]
        if args.encode:
            command.append("--encode")
    return command


def measure(args) -> dict:
    process = None
    lines: list[tuple[float, str]] = []
    ready = threading.Event()
    if args.engine != "idle":
        env = dict(os.environ, PYTHONPATH=str(ROOT), NO_COLOR="1")
        process = subprocess.Popen(_command(args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   cwd=ROOT, env=env)

        def read():
            for line in process.stdout:
                line = line.strip()
                if line == "ready":
                    ready.set()
                lines.append((time.monotonic(), line))

        threading.Thread(target=read, daemon=True).start()
        if not ready.wait(60):
            process.terminate()
            sys.exit("içerik 60 sn içinde yüklenmedi")
        print(f"yüklendi · {args.warmup:.0f} sn ısınma (pencerede ayar yapabilirsin)…", flush=True)
        time.sleep(args.warmup)
    root = process.pid if process else None

    start = time.monotonic()
    first = _processes()
    gpu, rss = [], []
    while time.monotonic() - start < args.duration:
        if process is not None and process.poll() is not None:
            break  # Esc
        time.sleep(1)
        value = _gpu_utilization()
        if value is not None:
            gpu.append(value)
        if root is not None:
            table = _processes()
            rss.append(sum(table[pid][2] for pid in _engine_pids(root, table)) / 1024)
    elapsed = time.monotonic() - start
    last = _processes()
    cpu = None
    if root is not None:
        pids = _engine_pids(root, last)
        used = sum(last[pid][1] - (first[pid][1] if pid in first and first[pid][3] == last[pid][3] else 0)
                   for pid in pids)
        cpu = used / elapsed * 100
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGTERM)
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()

    window = [line for at, line in lines if start <= at <= start + elapsed]
    fps = [float(m.group(1)) for line in window if (m := re.match(r"fps=([\d.]+)", line))]
    worst = [float(m.group(1)) for line in window if (m := re.search(r"worst_ms=([\d.]+)", line))]
    capture = [float(m.group(1)) for line in window if (m := re.match(r"capture_ms=([\d.]+)", line))]
    captured = [int(m.group(1)) for line in window if (m := re.search(r"frames=(\d+)", line))]
    skipped = [int(m.group(1)) for line in window if (m := re.search(r"skipped=(\d+)", line))]
    low = sorted(fps)[: max(1, len(fps) // 20)] if fps else []
    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "engine": args.engine,
        "label": args.label,
        "url": args.url if process else None,
        "boot": args.boot if process else None,
        "keys": args.keys,
        "capture": args.capture or None,
        "encode": bool(args.capture and args.encode),
        "seconds": round(elapsed, 1),
        "fps_avg": round(statistics.mean(fps), 1) if fps else None,
        "fps_low5": round(statistics.mean(low), 1) if low else None,
        "worst_frame_ms": round(max(worst), 1) if worst else None,
        "cpu_pct": round(cpu, 1) if cpu is not None else None,
        "rss_mb_avg": round(statistics.mean(rss)) if rss else None,
        "rss_mb_peak": round(max(rss)) if rss else None,
        "gpu_pct": round(statistics.mean(gpu), 1) if gpu else None,
        "capture_ms": round(statistics.mean(capture), 1) if capture else None,
        "captured_fps": round(statistics.mean(captured), 1) if captured else None,
        "skipped_per_s": round(statistics.mean(skipped), 1) if skipped else None,
    }


_COLUMNS = [("engine", "motor"), ("label", "etiket"), ("capture", "yayın"), ("fps_avg", "kare/sn"),
            ("fps_low5", "en düşük %5"), ("worst_frame_ms", "en uzun kare ms"), ("cpu_pct", "CPU %"),
            ("rss_mb_avg", "bellek MB"), ("rss_mb_peak", "tepe MB"), ("gpu_pct", "GPU %"),
            ("capture_ms", "yakala ms"), ("captured_fps", "yakalanan kare/sn")]


def _table(rows: list[dict]) -> str:
    def cell(row, key):
        value = row.get(key)
        if key == "capture" and value:
            return f"{value}{'+hevc' if row.get('encode') else ''}"
        return "–" if value in (None, "") else str(value)

    header = [title for _key, title in _COLUMNS]
    body = [[cell(row, key) for key, _title in _COLUMNS] for row in rows]
    widths = [max(len(r[i]) for r in [header, *body]) for i in range(len(header))]
    lines = ["  ".join(text.ljust(width) for text, width in zip(r, widths, strict=True)) for r in [header, *body]]
    lines.insert(1, "  ".join("─" * width for width in widths))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("engine", choices=["qt", "webkit", "idle", "compare"])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--boot", default="mic", help="mic (varsayılan) · system (yalnız qt) · none · JS dosyası")
    parser.add_argument("--keys", type=int, default=0, help="yüklenince içeriğe kaç kez → gönderilsin (mscillus modu)")
    parser.add_argument("--capture", type=int, default=0, metavar="FPS", help="Canard.saver hattını da çalıştır")
    parser.add_argument("--encode", action="store_true", help="--capture ile donanım HEVC kodlaması da")
    parser.add_argument("--warmup", type=float, default=15)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--label", default="")
    parser.add_argument("--force", action="store_true", help="Canard açıkken de ölç (sonuçlar kirlenir)")
    args = parser.parse_args()

    if args.engine == "compare":
        if not RESULTS.exists():
            print("henüz sonuç yok")
            return 0
        rows = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
        print(_table(rows))
        return 0
    if _canard_running() and not args.force:
        sys.exit("Canard çalışıyor: ölçümü kirletir. Önce menüden Çık (ya da --force).")

    result = measure(args)
    OUT.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(_table([result]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
