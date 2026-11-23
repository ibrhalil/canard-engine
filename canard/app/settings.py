import json
import os
import tempfile
from pathlib import Path

SETTINGS_VERSION = 1


def data_location() -> Path:
    """Canard'ın veri klasörü (macOS: ~/Library/Application Support/Canard)."""
    from PySide6.QtCore import QStandardPaths

    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def default_settings_path() -> Path:
    return data_location() / "settings.json"


class SettingsStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        self._data = {}
        if self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value
