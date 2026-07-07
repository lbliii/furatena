"""Persistence boundary for author source mutations.

Store implementations own compare-and-swap semantics.  Callers must treat a
returned revision as an opaque precondition and supply it when replacing
source.  A durable backend must preserve both source and revision ordering
across process restarts; the in-memory backend intentionally preserves neither
and is suitable only for tests, previews, and embedding in a single process.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Protocol


def source_revision(source: str) -> str:
    """Return the stable revision token used by author store backends."""
    return f"sha256:{hashlib.sha256(source.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class AuthorSourceSnapshot:
    """One source value and the revision required to replace it."""

    path: Path
    source: str
    revision: str


class AuthorStoreError(Exception):
    """Base class for expected author store failures."""


class AuthorSourceNotFound(AuthorStoreError):
    """Raised when a source path does not exist in the store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"author source not found: {path}")


class AuthorSourceConflict(AuthorStoreError):
    """Raised when create or compare-and-swap would overwrite newer source."""

    def __init__(
        self,
        path: Path,
        *,
        expected_revision: str | None,
        current_revision: str | None,
    ) -> None:
        self.path = path
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__(f"author source revision conflict: {path}")


class AuthorMutationStore(Protocol):
    """Source store contract for author reads and atomic mutations.

    ``replace`` is a compare-and-swap operation: implementations must compare
    and write within one synchronization boundary. Durable implementations are
    additionally responsible for preserving committed source across restarts
    and coordinating writers that do not share an in-process lock.
    """

    def exists(self, path: Path) -> bool:
        """Return whether ``path`` currently names a source record."""

    def read(self, path: Path) -> AuthorSourceSnapshot:
        """Read source or raise :class:`AuthorSourceNotFound`."""

    def create(self, path: Path, source: str) -> AuthorSourceSnapshot:
        """Create source or raise :class:`AuthorSourceConflict` if it exists."""

    def replace(
        self,
        path: Path,
        source: str,
        *,
        expected_revision: str | None,
    ) -> AuthorSourceSnapshot:
        """Atomically replace source when its current revision is expected."""


class InMemoryAuthorMutationStore:
    """Free-threading-safe, process-local author source store.

    Records disappear with this object and are not shared across processes.
    The lock is required on CPython free-threading builds; correctness never
    depends on the GIL serializing dictionary access.
    """

    def __init__(self, records: Mapping[Path, str] | None = None) -> None:
        self._sources = {_path_key(path): source for path, source in (records or {}).items()}
        self._lock = RLock()

    def exists(self, path: Path) -> bool:
        with self._lock:
            return _path_key(path) in self._sources

    def read(self, path: Path) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            try:
                source = self._sources[key]
            except KeyError as exc:
                raise AuthorSourceNotFound(key) from exc
            return _snapshot(key, source)

    def create(self, path: Path, source: str) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            if key in self._sources:
                current = self._sources[key]
                raise AuthorSourceConflict(
                    key,
                    expected_revision=None,
                    current_revision=source_revision(current),
                )
            self._sources[key] = source
            return _snapshot(key, source)

    def replace(
        self,
        path: Path,
        source: str,
        *,
        expected_revision: str | None,
    ) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            try:
                current = self._sources[key]
            except KeyError as exc:
                raise AuthorSourceNotFound(key) from exc
            current_revision = source_revision(current)
            if expected_revision != current_revision:
                raise AuthorSourceConflict(
                    key,
                    expected_revision=expected_revision,
                    current_revision=current_revision,
                )
            self._sources[key] = source
            return _snapshot(key, source)


class FilesystemAuthorMutationStore:
    """Behavior-compatible store for source files on the local filesystem.

    The store is durable across process restarts. Its lock makes operations
    atomic among threads sharing this instance; a future database or
    filesystem-locking backend is required for coordinated multi-process or
    distributed writers.
    """

    def __init__(self) -> None:
        self._lock = RLock()

    def exists(self, path: Path) -> bool:
        with self._lock:
            return _path_key(path).is_file()

    def read(self, path: Path) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            return self._read_unlocked(key)

    def create(self, path: Path, source: str) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            if key.exists():
                current_revision = (
                    source_revision(key.read_text(encoding="utf-8")) if key.is_file() else None
                )
                raise AuthorSourceConflict(
                    key,
                    expected_revision=None,
                    current_revision=current_revision,
                )
            key.parent.mkdir(parents=True, exist_ok=True)
            key.write_text(source, encoding="utf-8")
            return _snapshot(key, source)

    def replace(
        self,
        path: Path,
        source: str,
        *,
        expected_revision: str | None,
    ) -> AuthorSourceSnapshot:
        key = _path_key(path)
        with self._lock:
            current = self._read_unlocked(key)
            if expected_revision != current.revision:
                raise AuthorSourceConflict(
                    key,
                    expected_revision=expected_revision,
                    current_revision=current.revision,
                )
            key.write_text(source, encoding="utf-8")
            return _snapshot(key, source)

    @staticmethod
    def _read_unlocked(path: Path) -> AuthorSourceSnapshot:
        if not path.is_file():
            raise AuthorSourceNotFound(path)
        return _snapshot(path, path.read_text(encoding="utf-8"))


DEFAULT_AUTHOR_MUTATION_STORE = FilesystemAuthorMutationStore()


def _path_key(path: Path) -> Path:
    return path.expanduser().resolve()


def _snapshot(path: Path, source: str) -> AuthorSourceSnapshot:
    return AuthorSourceSnapshot(path=path, source=source, revision=source_revision(source))
