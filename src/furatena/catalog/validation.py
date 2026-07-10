"""Generation-scoped docs validation snapshots."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from furatena.catalog.check import check_catalog_configuration, check_catalog_content


@dataclass(frozen=True, slots=True)
class ValidationSnapshot:
    """Immutable validation result for one catalog/configuration generation."""

    catalog_generation: int
    configuration_fingerprint: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


class ValidationSnapshotService:
    """Publish one thread-safe validation result per effective generation."""

    def __init__(
        self,
        catalog: Any,
        *,
        views: Any = None,
        docs: Any = None,
        theme: Any = None,
        template_env: Callable[[], Any | None] | None = None,
    ) -> None:
        self.catalog = catalog
        self.views = views
        self.docs = docs
        self.theme = theme
        self._template_env = template_env or (lambda: None)
        self._lock = RLock()
        self._content_results: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}
        self._configuration_results: dict[
            str, tuple[tuple[str, ...], tuple[str, ...]]
        ] = {}
        self._snapshots: dict[tuple[int, str], ValidationSnapshot] = {}

    def snapshot(self, *, force: bool = False) -> ValidationSnapshot:
        """Return the current snapshot, recomputing once after invalidation."""
        with self._lock:
            while True:
                generation = int(self.catalog.generation)
                fingerprint = self.configuration_fingerprint()
                key = (generation, fingerprint)
                cached = self._snapshots.get(key)
                if cached is not None and not force:
                    return cached
                snapshot = self._compute_snapshot(
                    generation,
                    fingerprint,
                    force=force,
                )
                if (
                    generation == int(self.catalog.generation)
                    and fingerprint == self.configuration_fingerprint()
                ):
                    self._content_results = {
                        generation: self._content_results[generation]
                    }
                    self._configuration_results = {
                        fingerprint: self._configuration_results[fingerprint]
                    }
                    self._snapshots = {key: snapshot}
                    return snapshot
                force = False

    def _compute_snapshot(
        self,
        generation: int,
        fingerprint: str,
        *,
        force: bool,
    ) -> ValidationSnapshot:
        if force or generation not in self._content_results:
            errors, warnings = check_catalog_content(
                self.catalog,
                views=self.views,
                inventory_store=self.catalog.inventory_store,
            )
            self._content_results[generation] = (tuple(errors), tuple(warnings))
        if force or fingerprint not in self._configuration_results:
            errors, warnings = check_catalog_configuration(
                self.catalog,
                views=self.views,
                docs=self.docs,
                theme=self.theme,
                template_env=self._template_env(),
            )
            self._configuration_results[fingerprint] = (tuple(errors), tuple(warnings))
        content_errors, content_warnings = self._content_results[generation]
        config_errors, config_warnings = self._configuration_results[fingerprint]
        return ValidationSnapshot(
            catalog_generation=generation,
            configuration_fingerprint=fingerprint,
            errors=tuple(sorted((*content_errors, *config_errors))),
            warnings=tuple(sorted((*content_warnings, *config_warnings))),
        )

    def configuration_fingerprint(self) -> str:
        """Fingerprint docs/theme/template inputs without reading file contents."""
        digest = hashlib.sha256(repr(self.docs).encode("utf-8"))
        for path in self._configuration_paths():
            try:
                stat = path.stat()
            except OSError:
                digest.update(f"missing:{path}".encode())
                continue
            digest.update(str(path).encode("utf-8"))
            digest.update(f":{stat.st_mtime_ns}:{stat.st_size}".encode("ascii"))
        return f"sha256:{digest.hexdigest()}"

    def _configuration_paths(self) -> tuple[Path, ...]:
        if self.docs is None:
            return ()
        files = {
            self.docs.root / "docs.yaml",
            self.docs.mounts_path,
            self.docs.rewrites_path,
            self.docs.inventories_path,
        }
        roots = {
            self.docs.templates_dir,
            self.docs.framework_templates_dir,
            self.docs.theme_dir,
            *(getattr(self.theme, "template_roots", ()) or ()),
        }
        paths = {Path(path).resolve() for path in files if path is not None}
        for root in roots:
            root = Path(root).resolve()
            if root.is_dir():
                paths.update(path.resolve() for path in root.rglob("*") if path.is_file())
            else:
                paths.add(root)
        return tuple(sorted(paths, key=str))
