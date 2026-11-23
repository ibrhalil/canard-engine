import logging

from PySide6.QtWebEngineCore import QWebEnginePermission

from canard.web.base import notify_media_request

log = logging.getLogger("canard.web")

_ALLOWED = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
    QWebEnginePermission.PermissionType.DesktopVideoCapture,
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture,
}

_MICROPHONE = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
}


def decide(permission) -> None:
    kind = permission.permissionType()
    if kind in _MICROPHONE:
        notify_media_request("microphone")
    origin = permission.origin().toString()
    if kind in _ALLOWED:
        log.debug("permission granted: %s (%s)", kind.name, origin)
        permission.grant()
    else:
        log.info("permission denied: %s (%s)", kind.name, origin)
        permission.deny()
