from PySide6.QtWebEngineCore import QWebEnginePermission

_ALLOWED = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
    QWebEnginePermission.PermissionType.DesktopVideoCapture,
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture,
}


def decide(permission) -> None:
    if permission.permissionType() in _ALLOWED:
        permission.grant()
    else:
        permission.deny()
