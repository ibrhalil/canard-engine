import argparse
import logging
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication

import canard
from canard import i18n
from canard.app import logs
from canard.app.application import LANGUAGE_KEY, CanardApp
from canard.app.settings import SettingsStore, default_settings_path
from canard.content.sources import SourceError, from_build, from_url, validate
from canard.i18n import t

log = logging.getLogger("canard")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canard", description=t("Canard — live wallpaper and screensaver engine"))
    parser.add_argument("--mode", choices=["preview", "wallpaper", "screensaver"],
                        help=t("preview: window (default) · wallpaper: live wallpaper · screensaver: screensaver only"))
    parser.add_argument("--tray", action="store_true",
                        help=t("Menu bar app: settings are made in the menu and remembered "
                               "(not combined with --mode, --interactive, etc.)"))
    parser.add_argument("--url", help=t("Remote or dev server URL (http/https)"))
    parser.add_argument("--path", help=t("Local production build folder (containing index.html)"))
    parser.add_argument("--source", help=t("Saved source ID"))
    parser.add_argument("--list", action="store_true", help=t("List saved sources"))
    parser.add_argument("--remove", metavar="ID", help=t("Remove a saved source"))
    parser.add_argument("--interactive", action="store_true", help=t("Wallpaper receives mouse input (interactive mode)"))
    parser.add_argument("--boot-file", help=t("JS file to run when the page loads (boot script)"))
    parser.add_argument("--clear-boot", action="store_true", help=t("Remove the boot script of the source"))
    parser.add_argument("--no-system-audio", action="store_true",
                        help=t("Do not give system audio to the content (disables the getDisplayMedia audio bridge)"))
    parser.add_argument("--screensaver-after", type=float, metavar=t("MIN"),
                        help=t("Canard's own idle fullscreen after this many idle minutes (wallpaper or screensaver "
                               "mode). For the real system screensaver use Canard.saver, see docs/USAGE.md"))
    parser.add_argument("--no-saver-stream", action="store_true",
                        help=t("Do not stream frames to Canard.saver (macOS screensaver)"))
    parser.add_argument("--saver-fps", type=int, metavar="N",
                        help=t("Canard.saver stream frame rate (default 30; drops automatically if it cannot keep up)"))
    parser.add_argument("--settings", help=t("Settings file path (default: application data folder)"))
    parser.add_argument("--language", choices=[i18n.SYSTEM, *i18n.LANGUAGES],
                        help=t("Interface language for this run (default: menu setting, otherwise system)"))
    parser.add_argument("--log-level", choices=list(logs.LEVELS), default="info",
                        help=t("Terminal log level (default: info)"))
    parser.add_argument("-v", "--verbose", action="store_const", const="debug", dest="log_level",
                        help=t("Verbose log (--log-level debug)"))
    parser.add_argument("--log-file", nargs="?", const="", metavar=t("PATH"),
                        help=t("Also write the log to a file (always debug level); "
                               "default ~/Library/Logs/Canard/canard.log"))
    return parser


def _apply_language(argv) -> None:
    """Yardım metni de çevrilsin diye dil, ayrıştırmadan önce belirlenir: --language > ayar > sistem."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--language")
    pre.add_argument("--settings")
    known, _rest = pre.parse_known_args(argv)
    choice = known.language
    if choice is None:
        try:
            choice = SettingsStore(Path(known.settings) if known.settings else default_settings_path()).get(LANGUAGE_KEY)
        except OSError:
            choice = None
    i18n.set_language(i18n.resolve(choice))


def _register_url(app: CanardApp, url: str):
    source = app.registry.find(url=url)
    if source is None:
        source = app.registry.add(from_url(url))
        log.info("source added: %s (%s)", source.id, source.name)
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
        log.info("source added: %s (%s)", source.id, source.name)
    app.registry.set_active(source.id)
    return source


def _acquire_instance_lock():
    # İkinci örnek web profilinin kilidini alamaz (localStorage yalnız bellekte kalır), kısayollar da çakışır.
    from PySide6.QtCore import QLockFile

    from canard.web.engine import data_location

    directory = data_location()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "canard.lock"))
    if lock.tryLock(0):
        return lock
    info = lock.getLockInfo()  # PySide6: (pid, host, app)
    pid = info[0] if isinstance(info, tuple) and info and isinstance(info[0], int) else 0
    if pid:
        log.error("%s", t("Canard is already running (PID {pid}). Close it first (Ctrl+C) or: kill {pid}", pid=pid))
    else:
        log.error("%s", t("Canard is already running. Close it first."))
    return None


def main(argv=None) -> int:
    QCoreApplication.setApplicationName("Canard")
    QCoreApplication.setOrganizationName("Canard")
    _apply_language(argv)

    args = build_parser().parse_args(argv)
    log_file = args.log_file
    if args.tray and log_file is None:
        log_file = ""  # menü çubuğu uygulamasının terminali yok
    logs.setup(args.log_level, None if log_file is None else (log_file or logs.default_log_path()))

    if args.list or args.remove:
        app = CanardApp(args.settings)
        if args.remove:
            app.registry.remove(args.remove)
            print(t("Removed: {id}", id=args.remove))
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
            log.error("%s", t("{flags} cannot be used with --tray: these settings are made in the menu and remembered",
                                flags=", ".join(conflicting)))
            return 2
    mode = args.mode or "preview"
    if args.saver_fps is not None and not 1 <= args.saver_fps <= 60:
        log.error("%s", t("--saver-fps must be between 1 and 60"))
        return 2
    if args.screensaver_after is not None:
        if mode == "preview":
            log.error("%s", t("--screensaver-after only works with --mode wallpaper or --mode screensaver"))
            return 2
        if args.screensaver_after <= 0:
            log.error("%s", t("--screensaver-after must be greater than zero (minutes)"))
            return 2

    instance_lock = _acquire_instance_lock()
    if instance_lock is None:
        return 1

    app = CanardApp(args.settings)
    log.debug("Canard %s · Python %s · language %s · settings: %s", canard.__version__, sys.version.split()[0],
              i18n.language, app.settings_path)

    if args.url:
        source = _register_url(app, args.url)
    elif args.path:
        source = _register_build(app, args.path)
    elif args.source:
        source = app.registry.get(args.source)
        if source is None:
            log.error("%s", t("Source not found: {id}", id=args.source))
            return 1
        app.registry.set_active(source.id)
    else:
        source = app.registry.active()

    if source is None:
        if args.tray:
            log.info("%s", t("No source yet: add one from the menu (Source › Add URL… / Add Folder…)"))
            return _run(app, instance_lock, None, args, mode)
        log.error("%s", t("No source. To start: canard --url https://...  or  canard --path ./dist"))
        return 1

    if args.clear_boot:
        source.boot_script = None
        app.registry.update(source)
        log.info("%s", t("Boot script removed"))
    elif args.boot_file:
        boot_path = Path(args.boot_file).expanduser()
        if not boot_path.is_file():
            log.error("%s", t("Boot file not found: {path}", path=boot_path))
            return 1
        source.boot_script = boot_path.read_text(encoding="utf-8")
        app.registry.update(source)
        log.info("%s", t("Boot script attached: {path}", path=boot_path))

    if not args.tray:
        try:
            url = app.resolve(source)
        except SourceError as error:
            log.error("%s", error)
            return 1
        if source.boot_script:
            log.info("%s", t("Boot script active (remove with --clear-boot)"))
        log.info("starting: mode=%s · source=%s (%s) · %s", mode, source.name, source.type, url)
    return _run(app, instance_lock, source, args, mode)


def _run(app: CanardApp, instance_lock, source, args, mode: str) -> int:
    if args.tray:
        log.info("starting: menu bar")
    try:
        return app.run(source, mode=mode, interactive=args.interactive, system_audio=not args.no_system_audio,
                       screensaver_after=args.screensaver_after, saver_stream=not args.no_saver_stream,
                       saver_fps=args.saver_fps, tray=args.tray)
    finally:
        instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
