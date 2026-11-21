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


def decide(permission) -> None:
    kind = permission.permissionType()
    origin = permission.origin().toString()
    if kind in _ALLOWED:
        log.debug("izin verildi: %s (%s)", kind.name, origin)
        permission.grant()
    else:
        log.info("izin reddedildi: %s (%s)", kind.name, origin)
        permission.deny()
