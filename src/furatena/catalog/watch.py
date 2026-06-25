"""Background source watcher — marks dirty paths without stat-scanning every request."""

from __future__ import annotations

import threading
import time
from pathlib import Path

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", "frozen", ".docs-cache"})


class SourceWatcher:
    """Poll content roots for source file changes (no extra dependencies)."""

    def __init__(
        self,
        roots: tuple[Path, ...],
        *,
        interval: float = 1.0,
        extensions: frozenset[str] | None = None,
    ) -> None:
        self.roots = tuple(root.resolve() for root in roots if root.is_dir())
        self.interval = interval
        self.extensions = extensions or frozenset({".md"})
        self._dirty: set[Path] = set()
        self._lock = threading.Lock()
        self._mtimes: dict[Path, float] = {}
        self._tracked: set[Path] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._rescan()

    def start(self) -> None:
        if self._thread is not None or not self.roots:
            return
        self._thread = threading.Thread(target=self._loop, name="fura-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def note_dirty(self, path: Path) -> None:
        path = path.resolve()
        with self._lock:
            self._dirty.add(path)

    def drain_dirty(self) -> set[Path]:
        with self._lock:
            dirty = set(self._dirty)
            self._dirty.clear()
            return dirty

    def _loop(self) -> None:
        tick = 0
        while not self._stop.wait(self.interval):
            tick += 1
            if tick % 30 == 0:
                self._rescan()
            else:
                self._poll_tracked()

    def _iter_sources(self) -> list[Path]:
        files: list[Path] = []
        for root in self.roots:
            for ext in self.extensions:
                for path in root.rglob(f"*{ext}"):
                    if any(part in _SKIP_DIRS for part in path.parts):
                        continue
                    files.append(path.resolve())
        return files

    def _rescan(self) -> None:
        current = set(self._iter_sources())
        removed = self._tracked - current
        with self._lock:
            self._dirty |= removed
            for path in removed:
                self._mtimes.pop(path, None)
        for path in current:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            prev = self._mtimes.get(path)
            if prev is None:
                self._mtimes[path] = mtime
            elif mtime != prev:
                self._mtimes[path] = mtime
                with self._lock:
                    self._dirty.add(path)
        self._tracked = current

    def _poll_tracked(self) -> None:
        for path in self._tracked:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                with self._lock:
                    self._dirty.add(path)
                continue
            prev = self._mtimes.get(path)
            if prev is not None and mtime != prev:
                self._mtimes[path] = mtime
                with self._lock:
                    self._dirty.add(path)
