"""Log kurulumu: terminal (seviye seçilebilir, renkli) + isteğe bağlı dosya (her zaman debug). Qt mesajları `qt`,
sayfa konsolu `web` adıyla aynı kanala akar."""

import faulthandler
import logging
import logging.handlers
import os
import sys
from pathlib import Path

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

_COLORS = {logging.DEBUG: "\033[2m", logging.WARNING: "\033[33m", logging.ERROR: "\033[31m",
           logging.CRITICAL: "\033[1;31m"}
_RESET = "\033[0m"
_LABELS = {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARN", "ERROR": "ERROR", "CRITICAL": "CRIT"}

# Zararsız ama gürültülü Qt kategorileri: yalnız debug'da.
_QT_NOISE = ("qt.qpa.fonts", "propagateSizeHints", "qt.webenginecontext", "SKIA:")

_file_stream = None


def default_log_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "Canard" / "canard.log"
    from canard.web.engine import data_location

    return data_location() / "canard.log"


class _Formatter(logging.Formatter):
    def __init__(self, color: bool, with_date: bool):
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        name = record.name.removeprefix("canard.").removeprefix("canard")
        label = _LABELS.get(record.levelname, record.levelname)
        line = f"{self.formatTime(record, self.datefmt)} {label:<6} {name or 'app':<8} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if self._color and record.levelno in _COLORS:
            line = f"{_COLORS[record.levelno]}{line}{_RESET}"
        return line


def setup(level: str = "info", log_file: Path | None = None) -> None:
    global _file_stream
    root = logging.getLogger("canard")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.propagate = False

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(LEVELS[level])
    use_color = sys.stderr.isatty() and "NO_COLOR" not in os.environ
    console.setFormatter(_Formatter(color=use_color, with_date=False))
    root.addHandler(console)

    if log_file is not None:
        log_file = Path(log_file).expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_Formatter(color=False, with_date=True))
        root.addHandler(handler)
        _file_stream = open(log_file, "a", encoding="utf-8")
        faulthandler.enable(file=_file_stream, all_threads=True)
    else:
        faulthandler.enable(all_threads=True)

    sys.excepthook = _excepthook
    # Chromium stderr günlüğü yalnız debug'da (0=INFO … 3=FATAL).
    chromium_level = "1" if level == "debug" else "3"
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--log-level" not in flags:
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} --log-level={chromium_level}".strip()

    if log_file is not None:
        logging.getLogger("canard").debug("log file: %s", log_file)


def install_qt_handler() -> None:
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler

    qt_log = logging.getLogger("canard.qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(msg_type, context, message):
        level = levels.get(msg_type, logging.WARNING)
        category = getattr(context, "category", "") or ""
        if level == logging.WARNING and any(noise in category or noise in message for noise in _QT_NOISE):
            level = logging.DEBUG
        qt_log.log(level, "%s%s", f"{category}: " if category and category != "default" else "", message)

    qInstallMessageHandler(handler)


def _excepthook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    logging.getLogger("canard").critical("uncaught exception", exc_info=(exc_type, exc, tb))
