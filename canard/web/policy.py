import logging

from PySide6.QtWebEngineCore import QWebEnginePermission

log = logging.getLogger("canard.web")

_ALLOWED = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
    QWebEnginePermission.PermissionType.DesktopVideoCapture,
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture,
}


# Sayfa mikrofon/kamera isteyince çağrılır (ör. Runtime: işletim sistemi izni kapalıysa kullanıcıya söylemek için).
media_listeners: list = []
_MICROPHONE = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
}


def decide(permission) -> None:
    kind = permission.permissionType()
    if kind in _MICROPHONE:
        for listener in media_listeners:
            listener("microphone")
    origin = permission.origin().toString()
    if kind in _ALLOWED:
        log.debug("izin verildi: %s (%s)", kind.name, origin)
        permission.grant()
    else:
        log.info("izin reddedildi: %s (%s)", kind.name, origin)
        permission.deny()
