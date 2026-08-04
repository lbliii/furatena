"""Validated platform, site, state, and output roots for one application."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.exceptions import CatalogConfigError


class ApplicationRootError(CatalogConfigError):
    """Application roots do not satisfy the selected composition profile."""


_PROTECTED_SITE_NAMES = frozenset(
    {
        ".docs-cache",
        ".preview",
        "active",
        "build",
        "dist",
        "frozen",
        "generations",
        "last-known-good",
        "leases",
        "public",
        "receipts",
        "staging",
        "state",
    }
)


@dataclass(frozen=True, slots=True)
class ApplicationRoots:
    """One deterministic composition of immutable and writable application roots."""

    site: Path
    platform: Path
    state: Path
    output: Path
    managed: bool = False

    @classmethod
    def from_environment(
        cls,
        site_root: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> ApplicationRoots:
        values = os.environ if environ is None else environ
        site = site_root.expanduser().resolve()
        managed = bool(values.get("FURA_CONTENT_REPOSITORY", "").strip())
        platform_raw = values.get("FURA_PLATFORM_ROOT", "").strip()
        state_raw = values.get("FURA_RUNTIME_STATE_ROOT", "").strip()
        output_raw = values.get("FURA_OUTPUT_ROOT", "").strip()
        if managed:
            missing = [
                name
                for name, value in (
                    ("FURA_PLATFORM_ROOT", platform_raw),
                    ("FURA_RUNTIME_STATE_ROOT", state_raw),
                    ("FURA_OUTPUT_ROOT", output_raw),
                )
                if not value
            ]
            if missing:
                raise ApplicationRootError(
                    "Managed content requires explicit immutable and writable roots before startup: "
                    + ", ".join(missing)
                    + "."
                )
        platform = _root_path(platform_raw, default=site)
        state = _root_path(state_raw, default=site / ".docs-cache")
        output = _root_path(output_raw, default=site)
        roots = cls(site=site, platform=platform, state=state, output=output, managed=managed)
        roots.validate()
        return roots

    def validate(self) -> None:
        for label, path in (
            ("site", self.site),
            ("platform", self.platform),
            ("state", self.state),
            ("output", self.output),
        ):
            if not path.is_absolute():
                raise ApplicationRootError(
                    f"The configured {label} application root must use an absolute path: {path}."
                )
        if not self.managed:
            return
        if self.platform == self.site or self.platform.is_relative_to(self.site):
            raise ApplicationRootError(
                "The managed platform root must be immutable and outside the active site generation."
            )
        for label, writable in (("state", self.state), ("output", self.output)):
            if writable == self.site or writable.is_relative_to(self.site):
                raise ApplicationRootError(
                    f"The managed {label} root must be outside the read-only site generation: "
                    f"{writable}."
                )
            if writable == self.platform or writable.is_relative_to(self.platform):
                raise ApplicationRootError(
                    f"The managed {label} root must not write beneath the immutable platform root: "
                    f"{writable}."
                )
        if self.state == self.output:
            raise ApplicationRootError(
                "The managed state and output roots must remain distinct during operation."
            )

    def require_site_path(self, path: Path, *, label: str) -> Path:
        """Resolve one site-owned path, rejecting managed escape and protected roots."""
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = self.site / candidate
        resolved = candidate.resolve()
        if not self.managed:
            return resolved
        if not resolved.is_relative_to(self.site):
            raise ApplicationRootError(
                f"The managed {label} must remain beneath the active site root: {resolved}."
            )
        relative = resolved.relative_to(self.site)
        if any(part in _PROTECTED_SITE_NAMES for part in relative.parts):
            raise ApplicationRootError(
                f"The managed {label} uses a protected application namespace: {relative}."
            )
        current = self.site
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ApplicationRootError(
                    f"The managed {label} cannot traverse a symbolic link: {current}."
                )
        return resolved

    def ensure_writable_roots(self) -> None:
        """Create the two explicitly writable roots after validation."""
        self.state.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        for label, path in (("state", self.state), ("output", self.output)):
            if path.is_symlink():
                raise ApplicationRootError(
                    f"The configured {label} root cannot be a symbolic link: {path}."
                )
            if not os.access(path, os.W_OK):
                raise ApplicationRootError(
                    f"The configured {label} root must be writable before startup: {path}."
                )


def _root_path(raw: str, *, default: Path) -> Path:
    if not raw:
        return default.resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ApplicationRootError(
            f"The configured application root must use an absolute filesystem path: {raw}."
        )
    if path.is_symlink():
        raise ApplicationRootError(
            f"The configured application root cannot be a symbolic link: {path}."
        )
    return path.resolve()
