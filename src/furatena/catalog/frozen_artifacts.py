"""Read frozen bulk sidecars with stable HTTP validators."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

from chirp.http.response import Response

_PUBLIC_REVALIDATE = "public, max-age=0, must-revalidate"


@dataclass(frozen=True, slots=True)
class _FrozenArtifact:
    signature: tuple[int, int]
    body: bytes
    etag: str
    last_modified: str


class FrozenArtifactStore:
    """Serve identity-scoped freeze files without rebuilding their payloads."""

    __slots__ = ("_cache", "_lock", "root")

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._cache: dict[str, _FrozenArtifact] = {}
        self._lock = threading.Lock()

    def response(self, relative_path: str, *, content_type: str) -> Response | None:
        """Return frozen bytes and validators, or ``None`` when unavailable."""
        artifact = self._read(relative_path)
        if artifact is None:
            return None
        return Response(
            artifact.body,
            content_type=content_type,
            headers=(
                ("ETag", artifact.etag),
                ("Last-Modified", artifact.last_modified),
                ("Cache-Control", _PUBLIC_REVALIDATE),
            ),
        )

    def _read(self, relative_path: str) -> _FrozenArtifact | None:
        if self.root is None:
            return None
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        path = self.root / relative
        try:
            stat = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        signature = (stat.st_mtime_ns, stat.st_size)
        key = relative.as_posix()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached.signature == signature:
                return cached
            try:
                body = path.read_bytes()
                final_stat = path.stat()
            except OSError:
                return None
            signature = (final_stat.st_mtime_ns, final_stat.st_size)
            digest = hashlib.sha256(body).hexdigest()
            artifact = _FrozenArtifact(
                signature=signature,
                body=body,
                etag=f'"{digest}"',
                last_modified=format_datetime(
                    datetime.fromtimestamp(final_stat.st_mtime, tz=UTC).replace(microsecond=0),
                    usegmt=True,
                ),
            )
            self._cache[key] = artifact
            return artifact
