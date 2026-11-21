import argparse
import sys

from PySide6.QtCore import QCoreApplication

from canard.app.application import CanardApp
from canard.content.sources import SourceError, from_build, from_url, resolve_load_url, validate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canard", description="Canard — CANvas boARD engine")
    parser.add_argument("--mode", choices=["preview", "wallpaper", "screensaver"], default="preview",
                        help="Çalışma modu (wallpaper/screensaver Faz 2a/3a'da)")
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
    parser.add_argument("--settings", help="Ayar dosyası yolu (varsayılan: uygulama veri dizini)")
    return parser


def _register_url(app: CanardApp, url: str):
    source = app.registry.find(url=url)
    if source is None:
        source = app.registry.add(from_url(url))
        print(f"Kaynak eklendi: {source.id} ({source.name})")
    app.registry.set_active(source.id)
    return source


def _register_build(app: CanardApp, path: str):
    source = from_build(path)
    errors = validate(source)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        sys.exit(1)
    existing = app.registry.find(path=source.path)
    if existing is not None:
        source = existing
    else:
        source = app.registry.add(source)
        print(f"Kaynak eklendi: {source.id} ({source.name})")
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
    print(f"Canard zaten çalışıyor{hint}", file=sys.stderr)
    return None


def main(argv=None) -> int:
    QCoreApplication.setApplicationName("Canard")
    QCoreApplication.setOrganizationName("Canard")

    args = build_parser().parse_args(argv)

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

    if args.mode == "screensaver":
        print("Bu mod Faz 3a'da geliyor.", file=sys.stderr)
        return 2

    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        return 1

    app = CanardApp(args.settings)

    if args.url:
        source = _register_url(app, args.url)
    elif args.path:
        source = _register_build(app, args.path)
    elif args.source:
        source = app.registry.get(args.source)
        if source is None:
            print(f"Kaynak bulunamadı: {args.source}", file=sys.stderr)
            return 1
        app.registry.set_active(source.id)
    else:
        source = app.registry.active()

    if source is None:
        print("Kaynak yok. Başlatmak için: canard --url https://...  veya  canard --path ./dist", file=sys.stderr)
        return 1

    if args.clear_boot:
        source.boot_script = None
        app.registry.update(source)
        print("Boot script kaldırıldı")
    elif args.boot_file:
        from pathlib import Path

        boot_path = Path(args.boot_file).expanduser()
        if not boot_path.is_file():
            print(f"Boot dosyası yok: {boot_path}", file=sys.stderr)
            return 1
        source.boot_script = boot_path.read_text(encoding="utf-8")
        app.registry.update(source)
        print(f"Boot script bağlandı: {boot_path}")

    previous_port = source.port
    try:
        url = resolve_load_url(source, app.servers)
    except SourceError as error:
        print(str(error), file=sys.stderr)
        return 1
    if source.port != previous_port:
        if previous_port:
            print(f"Port {previous_port} dolu, {source.port} kullanılıyor (içeriğin yerel verileri sıfırlanır)",
                  file=sys.stderr)
        app.registry.update(source)

    if source.boot_script:
        print("Boot script aktif (kaldırmak için --clear-boot)")

    try:
        return app.run(url, mode=args.mode, boot_script=source.boot_script,
                       interactive=args.interactive, system_audio=not args.no_system_audio)
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
