import argparse
import logging
import sys

from PySide6.QtCore import QCoreApplication

import canard
from canard.app import logs
from canard.app.application import CanardApp
from canard.content.sources import SourceError, from_build, from_url, validate

log = logging.getLogger("canard")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canard", description="Canard — CANvas boARD engine")
    parser.add_argument("--mode", choices=["preview", "wallpaper", "screensaver"],
                        help="preview: pencere (varsayılan) · wallpaper: canlı duvar kağıdı · "
                             "screensaver: yalnız ekran koruyucu")
    parser.add_argument("--tray", action="store_true",
                        help="Menü çubuğu uygulaması: duvar kağıdı ve ekran koruyucu ayarları menüden, kalıcı "
                             "(çalışma seçenekleri --mode, --interactive vb. bununla kullanılmaz)")
    parser.add_argument("--url", help="Uzak veya dev sunucu URL'si (http/https)")
    parser.add_argument("--path", help="Yerel production build klasörü (index.html içeren)")
    parser.add_argument("--source", help="Kayıtlı kaynak kimliği")
    parser.add_argument("--list", action="store_true", help="Kayıtlı kaynakları listele")
    parser.add_argument("--remove", metavar="ID", help="Kaynak kimliğini sil")
    parser.add_argument("--interactive", action="store_true",
                        help="Duvar kağıdı fare olaylarını alsın (etkileşimli mod)")
    parser.add_argument("--boot-file", help="Sayfa yüklendiğinde çalışacak JS dosyası (boot script)")
    parser.add_argument("--clear-boot", action="store_true", help="Kaynağa bağlı boot script'i kaldır")
    parser.add_argument("--no-system-audio", action="store_true",
                        help="Sistem sesini içeriğe verme (getDisplayMedia ses köprüsünü kapat)")
    parser.add_argument("--screensaver-after", type=float, metavar="DK",
                        help="Canard'ın kendi boşta tam ekranı: bu kadar dakika boşta kalınca (wallpaper ya da "
                             "screensaver modunda). Gerçek sistem ekran koruyucusu için Canard.saver, bkz. KULLANIM")
    parser.add_argument("--no-saver-stream", action="store_true",
                        help="Canard.saver'a (macOS ekran koruyucu) kare yayınlama")
    parser.add_argument("--saver-fps", type=int, metavar="N",
                        help="Canard.saver yayın kare hızı (varsayılan 30; yetişilemezse kendiliğinden düşer)")
    parser.add_argument("--settings", help="Ayar dosyası yolu (varsayılan: uygulama veri dizini)")
    parser.add_argument("--log-level", choices=list(logs.LEVELS), default="info",
                        help="Terminal log düzeyi (varsayılan: info)")
    parser.add_argument("-v", "--verbose", action="store_const", const="debug", dest="log_level",
                        help="Ayrıntılı log (--log-level debug)")
    parser.add_argument("--log-file", nargs="?", const="", metavar="YOL",
                        help="Logu dosyaya da yaz (her zaman debug düzeyinde); yol verilmezse "
                             "~/Library/Logs/Canard/canard.log")
    return parser


def _register_url(app: CanardApp, url: str):
    source = app.registry.find(url=url)
    if source is None:
        source = app.registry.add(from_url(url))
        log.info("kaynak eklendi: %s (%s)", source.id, source.name)
    app.registry.set_active(source.id)
    return source


def _register_build(app: CanardApp, path: str):
    source = from_build(path)
    errors = validate(source)
    if errors:
        for error in errors:
            log.error("%s", error)
        sys.exit(1)
    existing = app.registry.find(path=source.path)
    if existing is not None:
        source = existing
    else:
        source = app.registry.add(source)
        log.info("kaynak eklendi: %s (%s)", source.id, source.name)
    app.registry.set_active(source.id)
    return source


def _acquire_instance_lock():
    # Tek örnek: ikinci Canard web profilinin leveldb kilidini alamaz ve localStorage'ı sessizce
    # yalnız bellekte tutar; global kısayollar da çakışır. Çöken sürecin kilidi Qt tarafından temizlenir.
    from PySide6.QtCore import QLockFile

    from canard.web.engine import data_location

    directory = data_location()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "canard.lock"))
    if lock.tryLock(0):
        return lock
    info = lock.getLockInfo()  # PySide6: (pid, host, app)
    pid = info[0] if isinstance(info, tuple) and info and isinstance(info[0], int) else 0
    hint = f" (PID {pid}). Önce onu kapat (Ctrl+C) ya da: kill {pid}" if pid else ". Önce onu kapat."
    log.error("Canard zaten çalışıyor%s", hint)
    return None


def main(argv=None) -> int:
    QCoreApplication.setApplicationName("Canard")
    QCoreApplication.setOrganizationName("Canard")

    args = build_parser().parse_args(argv)
    log_file = args.log_file
    if args.tray and log_file is None:
        log_file = ""  # menü çubuğu uygulamasının terminali yok: log her zaman dosyaya da yazılır
    logs.setup(args.log_level, None if log_file is None else (log_file or logs.default_log_path()))

    if args.list or args.remove:
        app = CanardApp(args.settings)
        if args.remove:
            app.registry.remove(args.remove)
            print(f"Silindi: {args.remove}")
        active_id = app.registry.active_id()
        for source in app.registry.list():
            mark = "*" if source.id == active_id else " "
            print(f"{mark} {source.id}  {source.type:<9}  {source.name}  {source.url or source.path}")
        return 0

    if args.tray:
        conflicting = [flag for flag, given in (
            ("--mode", args.mode), ("--interactive", args.interactive), ("--no-system-audio", args.no_system_audio),
            ("--screensaver-after", args.screensaver_after is not None), ("--no-saver-stream", args.no_saver_stream),
            ("--saver-fps", args.saver_fps is not None)) if given]
        if conflicting:
            log.error("--tray ile %s kullanılmaz: bu ayarlar menüden yapılır ve hatırlanır", ", ".join(conflicting))
            return 2
    mode = args.mode or "preview"
    if args.saver_fps is not None and not 1 <= args.saver_fps <= 60:
        log.error("--saver-fps 1–60 arasında olmalı")
        return 2
    if args.screensaver_after is not None:
        if mode == "preview":
            log.error("--screensaver-after yalnız --mode wallpaper ya da --mode screensaver ile kullanılır")
            return 2
        if args.screensaver_after <= 0:
            log.error("--screensaver-after sıfırdan büyük olmalı (dakika)")
            return 2

    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        return 1

    app = CanardApp(args.settings)
    log.debug("Canard %s · Python %s · ayarlar: %s", canard.__version__, sys.version.split()[0], app.settings_path)

    if args.url:
        source = _register_url(app, args.url)
    elif args.path:
        source = _register_build(app, args.path)
    elif args.source:
        source = app.registry.get(args.source)
        if source is None:
            log.error("kaynak bulunamadı: %s", args.source)
            return 1
        app.registry.set_active(source.id)
    else:
        source = app.registry.active()

    if source is None:
        if args.tray:
            log.info("kaynak yok: menüden ekle (Kaynak › URL ekle… / Klasör ekle…)")
            return _run(app, instance_lock, None, args, mode)
        log.error("kaynak yok. Başlatmak için: canard --url https://...  veya  canard --path ./dist")
        return 1

    if args.clear_boot:
        source.boot_script = None
        app.registry.update(source)
        log.info("boot script kaldırıldı")
    elif args.boot_file:
        from pathlib import Path

        boot_path = Path(args.boot_file).expanduser()
        if not boot_path.is_file():
            log.error("boot dosyası yok: %s", boot_path)
            return 1
        source.boot_script = boot_path.read_text(encoding="utf-8")
        app.registry.update(source)
        log.info("boot script bağlandı: %s", boot_path)

    if not args.tray:
        try:
            url = app.resolve(source)
        except SourceError as error:
            log.error("%s", error)
            return 1
        if source.boot_script:
            log.info("boot script aktif (kaldırmak için --clear-boot)")
        log.info("başlıyor: mod=%s · kaynak=%s (%s) · %s", mode, source.name, source.type, url)
    return _run(app, instance_lock, source, args, mode)


def _run(app: CanardApp, instance_lock, source, args, mode: str) -> int:
    if args.tray:
        log.info("başlıyor: menü çubuğu")
    try:
        return app.run(source, mode=mode, interactive=args.interactive, system_audio=not args.no_system_audio,
                       screensaver_after=args.screensaver_after, saver_stream=not args.no_saver_stream,
                       saver_fps=args.saver_fps, tray=args.tray)
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
