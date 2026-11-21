REMOTE = "remote"
BUILD = "build"
DEVSERVER = "devserver"

_TYPES = (REMOTE, BUILD, DEVSERVER)


class SourceError(Exception):
    pass


class ContentSource:
    def __init__(self, id: str, name: str, type: str, url: str | None = None,
                 path: str | None = None, boot_script: str | None = None, port: int | None = None):
        self.id = id
        self.name = name
        self.type = type
        self.url = url
        self.path = path
        self.boot_script = boot_script
        self.port = port

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "path": self.path,
            "boot_script": self.boot_script,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContentSource":
        return cls(
            id=data["id"],
            name=data["name"],
            type=data["type"],
            url=data.get("url"),
            path=data.get("path"),
            boot_script=data.get("boot_script"),
            port=data.get("port"),
        )

    def __repr__(self):
        return f"ContentSource(id={self.id!r}, name={self.name!r}, type={self.type!r})"


def from_url(url: str, name: str | None = None) -> ContentSource:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return ContentSource(
        id=_new_id(),
        name=name or parsed.hostname or url,
        type=REMOTE,
        url=url,
    )


def from_build(path: str, name: str | None = None) -> ContentSource:
    from pathlib import Path

    resolved = Path(path).expanduser().resolve()
    return ContentSource(
        id=_new_id(),
        name=name or resolved.name,
        type=BUILD,
        path=str(resolved),
    )


def validate(source: ContentSource) -> list[str]:
    from pathlib import Path
    from urllib.parse import urlparse

    errors: list[str] = []
    if source.type not in _TYPES:
        errors.append(f"Bilinmeyen kaynak türü: {source.type}")
        return errors
    if source.type == BUILD:
        if not source.path:
            errors.append("build kaynağı için klasör yolu gerekli")
        else:
            root = Path(source.path)
            if not root.is_dir():
                errors.append(f"Klasör yok: {root}")
            elif not (root / "index.html").is_file():
                errors.append(f"Klasörde index.html yok: {root}")
    else:
        if not source.url:
            errors.append(f"{source.type} kaynağı için URL gerekli")
        else:
            parsed = urlparse(source.url)
            if parsed.scheme not in ("http", "https"):
                errors.append("URL http/https şemasıyla başlamalı")
    return errors


def resolve_load_url(source: ContentSource, servers) -> str:
    errors = validate(source)
    if errors:
        raise SourceError("; ".join(errors))
    if source.type == BUILD:
        from pathlib import Path

        server = servers.server_for(Path(source.path), source.port or 0)
        source.port = server.port
        return server.url + "/"
    return source.url


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


class SourceRegistry:
    def __init__(self, store):
        self._store = store
        self._sources: dict[str, ContentSource] = {}
        for item in store.get("sources", []):
            try:
                source = ContentSource.from_dict(item)
            except (KeyError, TypeError):
                continue
            self._sources[source.id] = source

    def list(self) -> list[ContentSource]:
        return list(self._sources.values())

    def get(self, source_id: str) -> ContentSource | None:
        return self._sources.get(source_id)

    def find(self, url: str | None = None, path: str | None = None) -> ContentSource | None:
        for source in self._sources.values():
            if url is not None and source.url == url:
                return source
            if path is not None and source.path == path:
                return source
        return None

    def add(self, source: ContentSource) -> ContentSource:
        self._sources[source.id] = source
        self._persist()
        return source

    def update(self, source: ContentSource) -> None:
        if source.id not in self._sources:
            raise SourceError(f"Kaynak bulunamadı: {source.id}")
        self._sources[source.id] = source
        self._persist()

    def remove(self, source_id: str) -> None:
        self._sources.pop(source_id, None)
        if self.active_id() == source_id:
            self._store.set("active_source_id", None)
            self._store.save()
        self._persist()

    def active_id(self) -> str | None:
        return self._store.get("active_source_id")

    def active(self) -> ContentSource | None:
        return self._sources.get(self.active_id())

    def set_active(self, source_id: str) -> None:
        if source_id not in self._sources:
            raise SourceError(f"Kaynak bulunamadı: {source_id}")
        self._store.set("active_source_id", source_id)
        self._persist()

    def _persist(self) -> None:
        self._store.set("sources", [s.to_dict() for s in self._sources.values()])
        self._store.save()
