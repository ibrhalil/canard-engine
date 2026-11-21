import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

_MIME_OVERRIDES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json; charset=utf-8",
}

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 64 * 1024


class _Handler(BaseHTTPRequestHandler):
    server_version = "Canard"
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._serve(include_body=True)

    def do_HEAD(self):
        self._serve(include_body=False)

    def _serve(self, include_body: bool) -> None:
        root = Path(self.server.canard_root).resolve()
        target = self._resolve_target(root)
        if target is None:
            self._send_simple(403, b"Forbidden")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send_simple(404, b"Not Found")
            return
        size = target.stat().st_size
        byte_range = self._parse_range(size)
        if byte_range is False:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start, end = byte_range or (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(206 if byte_range else 200)
        self.send_header("Content-Type", self._guess_type(target))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if include_body and length:
            try:
                with target.open("rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining:
                        chunk = f.read(min(_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                # Medya öğeleri kısmi istekleri sık sık yarıda keser; hata değil.
                pass

    def _parse_range(self, size: int):
        """None: Range yok/yok sayıldı · (start, end): tek aralık · False: karşılanamaz (416)."""
        header = self.headers.get("Range")
        if not header:
            return None
        match = _RANGE.match(header.strip())
        if not match or (not match.group(1) and not match.group(2)):
            return None  # çoklu/bozuk aralık: tüm dosya gönderilir (RFC 9110'a uygun)
        first, last = match.group(1), match.group(2)
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            suffix = int(last)
            if suffix == 0:
                return False
            start, end = max(0, size - suffix), size - 1
        if start >= size or start > end:
            return False
        return start, end

    def _resolve_target(self, root: Path) -> Path | None:
        path = unquote(urlsplit(self.path).path)
        if "\x00" in path:
            return None
        candidate = (root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _guess_type(self, target: Path) -> str:
        override = _MIME_OVERRIDES.get(target.suffix.lower())
        if override:
            return override
        guessed, _ = mimetypes.guess_type(target.name)
        return guessed or "application/octet-stream"

    def _send_simple(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        import logging

        logging.getLogger("canard.server").debug("%s %s", self.address_string(), format % args)


class _Server(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        import sys

        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class StaticFileServer:
    def __init__(self, root: Path, port: int = 0):
        self.root = Path(root).resolve()
        # Sabit port → sabit origin (http://127.0.0.1:PORT): localStorage/çerezler yeniden başlatmada korunur.
        try:
            self._httpd = _Server(("127.0.0.1", port), _Handler)
        except OSError:
            self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.canard_root = str(self.root)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class ServerManager:
    def __init__(self):
        self._servers: dict[Path, StaticFileServer] = {}

    def server_for(self, root: Path, port: int = 0) -> StaticFileServer:
        key = Path(root).expanduser().resolve()
        server = self._servers.get(key)
        if server is None:
            server = StaticFileServer(key, port)
            server.start()
            self._servers[key] = server
        return server

    def stop_all(self) -> None:
        for server in self._servers.values():
            server.stop()
        self._servers.clear()
