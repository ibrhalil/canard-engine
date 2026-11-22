"""Canard'ın kendi boşta tam ekranı (`--screensaver-after`): sistem ekran koruyucusu ve kilitten önce devreye girer;
kilit, ekran uykusu ya da sistem ekran koruyucusu başlarsa hemen çekilir."""

import logging
import time

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger("canard.screensaver")

_IDLE_POLL_MS = 1000
_ACTIVE_POLL_MS = 150
_GRACE_SECONDS = 1.5


class ScreensaverController(QObject):
    def __init__(self, manager, adapter, after_seconds: float, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._adapter = adapter
        self._after = after_seconds
        self._active = False
        self._grace_until = 0.0
        self._last_idle = 0.0
        self._locked = False
        self._asleep = False
        self._blocked_reported = False
        self._focus = adapter.focus_keeper()
        self._watcher = adapter.watch_session(self._on_session)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(_IDLE_POLL_MS)
        log.info("starts after %s idle (start now with ⌃ + ⌥ + ⌘ + L)", _duration(after_seconds))

    @property
    def active(self) -> bool:
        return self._active

    def start_now(self) -> None:
        if self._locked or self._asleep:
            log.info("not started: session locked or display asleep")
            return
        self._activate("shortcut", grace=True)

    def close(self) -> None:
        self._timer.stop()
        self._deactivate("shutdown")
        self._watcher.close()

    def _tick(self) -> None:
        idle = self._adapter.idle_seconds()
        if self._active:
            if time.monotonic() < self._grace_until:
                self._last_idle = idle
            elif idle < self._last_idle:
                self._deactivate("user input")
            else:
                self._last_idle = idle
            return
        if idle < self._after:
            self._blocked_reported = False
            return
        if self._locked or self._asleep:
            return
        blockers = self._adapter.display_sleep_blockers()
        if blockers:
            if not self._blocked_reported:
                log.info("postponed: an app keeps the display awake (%s)", "; ".join(blockers))
                self._blocked_reported = True
            return
        self._activate(f"{_duration(idle)} idle")

    def _activate(self, reason: str, grace: bool = False) -> None:
        if self._active:
            return
        self._active = True
        # Kısayolla başlatınca tuşların bırakılması giriş sayılmasın.
        self._grace_until = time.monotonic() + _GRACE_SECONDS if grace else 0.0
        self._last_idle = self._adapter.idle_seconds()
        self._manager.set_screensaver(True)
        self._focus.enter()
        self._timer.setInterval(_ACTIVE_POLL_MS)
        log.info("started (%s)", reason)

    def _deactivate(self, reason: str) -> None:
        if not self._active:
            return
        self._active = False
        self._manager.set_screensaver(False)
        self._focus.leave()
        self._timer.setInterval(_IDLE_POLL_MS)
        log.info("ended (%s)", reason)

    def _on_session(self, name: str) -> None:
        log.debug("session event: %s", name)
        if name == "com.apple.screenIsLocked":
            self._locked = True
            self._deactivate("session locked")
        elif name == "com.apple.screenIsUnlocked":
            self._locked = False
        elif name == "com.apple.screensaver.didstart":
            self._deactivate("system screensaver started")
        elif name == "NSWorkspaceScreensDidSleepNotification":
            self._asleep = True
            self._deactivate("display asleep")
        elif name == "NSWorkspaceScreensDidWakeNotification":
            self._asleep = False


def _duration(seconds: float) -> str:
    return f"{seconds / 60:.1f} min" if seconds >= 60 else f"{seconds:.0f} s"
