"""Theme customization inspection and eject helpers."""

from __future__ import annotations

import difflib
import filecmp
import shutil
from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.config import DocsConfig
from furatena.catalog.theme import DocsTheme
from furatena.catalog.theme_pack import resolve_theme_paths

_TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".svg",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True, slots=True)
class ThemeCandidate:
    """One file or directory that can satisfy a logical theme path."""

    logical_path: str
    label: str
    kind: str
    path: Path
    exists: bool
    active: bool = False


@dataclass(frozen=True, slots=True)
class ThemeResolution:
    """Resolved source chain for a template or asset path."""

    logical_path: str
    candidates: tuple[ThemeCandidate, ...]
    override_path: Path

    @property
    def active(self) -> ThemeCandidate | None:
        return next((candidate for candidate in self.candidates if candidate.active), None)

    @property
    def overridden(self) -> bool:
        active = self.active
        return active is not None and active.path == self.override_path


@dataclass(frozen=True, slots=True)
class ThemeEjectResult:
    """Result of copying an upstream theme file into project overrides."""

    logical_path: str
    source_path: Path
    target_path: Path
    copied_files: int
    skipped: bool = False


def normalize_theme_logical_path(raw: str) -> str:
    """Return a safe slash-separated path inside a theme/template tree."""
    logical = raw.strip().replace("\\", "/").strip("/")
    if not logical:
        raise ValueError("theme path must not be empty")
    path = Path(logical)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid theme path: {raw!r}")
    return path.as_posix()


def _template_candidates(
    docs: DocsConfig, theme: DocsTheme, logical_path: str
) -> list[ThemeCandidate]:
    candidates: list[ThemeCandidate] = [
        ThemeCandidate(
            logical_path=logical_path,
            label="project",
            kind="template",
            path=(docs.templates_dir / logical_path).resolve(),
            exists=(docs.templates_dir / logical_path).exists(),
        )
    ]
    for root in theme.template_roots:
        label = "theme"
        rel_root = (
            root.relative_to(docs.root).as_posix() if _is_relative_to(root, docs.root) else ""
        )
        if rel_root == docs.theme.templates:
            label = "theme-template"
        elif root != docs.theme_dir:
            label = "skin"
        path = (root / logical_path).resolve()
        candidates.append(
            ThemeCandidate(
                logical_path=logical_path,
                label=label,
                kind="template",
                path=path,
                exists=path.exists(),
            )
        )
    path = (docs.framework_templates_dir / logical_path).resolve()
    candidates.append(
        ThemeCandidate(
            logical_path=logical_path,
            label="framework",
            kind="template",
            path=path,
            exists=path.exists(),
        )
    )
    return _mark_active(candidates)


def _asset_candidates(docs: DocsConfig, logical_path: str) -> list[ThemeCandidate]:
    skin = resolve_theme_paths(docs)
    rel = logical_path.removeprefix("assets/").strip("/")
    candidates: list[ThemeCandidate] = []
    app_path = (docs.theme_dir / "assets" / rel).resolve()
    candidates.append(
        ThemeCandidate(
            logical_path=logical_path,
            label="project",
            kind="asset",
            path=app_path,
            exists=app_path.exists(),
        )
    )
    if skin.pack is not None:
        pack_path = (skin.pack.root / "assets" / rel).resolve()
        candidates.append(
            ThemeCandidate(
                logical_path=logical_path,
                label="skin",
                kind="asset",
                path=pack_path,
                exists=pack_path.exists(),
            )
        )
    if skin.docs_core is not None:
        core_path = (skin.docs_core.root / rel).resolve()
        candidates.append(
            ThemeCandidate(
                logical_path=logical_path,
                label="docs-core",
                kind="asset",
                path=core_path,
                exists=core_path.exists(),
            )
        )
    return _mark_active(candidates)


def _mark_active(candidates: list[ThemeCandidate]) -> list[ThemeCandidate]:
    active_index = next((i for i, candidate in enumerate(candidates) if candidate.exists), None)
    if active_index is None:
        return candidates
    return [
        ThemeCandidate(
            logical_path=candidate.logical_path,
            label=candidate.label,
            kind=candidate.kind,
            path=candidate.path,
            exists=candidate.exists,
            active=index == active_index,
        )
        for index, candidate in enumerate(candidates)
    ]


def resolve_theme_customization(docs: DocsConfig, logical_path: str) -> ThemeResolution:
    """Resolve the currently active file and override target for a theme path."""
    logical = normalize_theme_logical_path(logical_path)
    theme = DocsTheme.from_docs_config(docs)
    if logical.startswith("assets/"):
        candidates = _asset_candidates(docs, logical)
        override_path = (docs.theme_dir / logical).resolve()
    else:
        candidates = _template_candidates(docs, theme, logical)
        override_path = _template_override_path(docs, logical)
    return ThemeResolution(
        logical_path=logical,
        candidates=tuple(candidates),
        override_path=override_path,
    )


def list_theme_customizations(docs: DocsConfig) -> tuple[ThemeResolution, ...]:
    """List active template paths that a user is likely to inspect or eject."""
    theme = DocsTheme.from_docs_config(docs)
    logical_paths: set[str] = set()
    for root in (*theme.template_roots, docs.templates_dir, docs.framework_templates_dir):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in _TEXT_SUFFIXES:
                logical_paths.add(path.relative_to(root).as_posix())
    for rel in ("assets/branding", "assets/fonts", "assets/icons"):
        resolution = resolve_theme_customization(docs, rel)
        if any(candidate.exists for candidate in resolution.candidates):
            logical_paths.add(rel)
    return tuple(resolve_theme_customization(docs, logical) for logical in sorted(logical_paths))


def eject_theme_path(
    docs: DocsConfig,
    logical_path: str,
    *,
    force: bool = False,
) -> ThemeEjectResult:
    """Copy the active upstream source into the project's override location."""
    resolution = resolve_theme_customization(docs, logical_path)
    active = resolution.active
    if active is None:
        raise FileNotFoundError(f"theme path not found: {resolution.logical_path}")
    target = resolution.override_path
    if target.exists() and not force:
        if active.path == target:
            return ThemeEjectResult(
                logical_path=resolution.logical_path,
                source_path=active.path,
                target_path=target,
                copied_files=0,
                skipped=True,
            )
        raise FileExistsError(f"{target} already exists (use --force to overwrite)")
    copied = _copy_with_provenance(
        active.path, target, resolution.logical_path, source_label=active.label
    )
    return ThemeEjectResult(
        logical_path=resolution.logical_path,
        source_path=active.path,
        target_path=target,
        copied_files=copied,
    )


def diff_theme_path(docs: DocsConfig, logical_path: str) -> str:
    """Return a unified diff between the override file and the next upstream source."""
    resolution = resolve_theme_customization(docs, logical_path)
    target = resolution.override_path
    if not target.is_file():
        raise FileNotFoundError(f"override not found: {target}")
    upstream = _next_upstream(resolution, target)
    if upstream is None or not upstream.path.is_file():
        raise FileNotFoundError(f"upstream source not found for {resolution.logical_path}")
    if target.suffix not in _TEXT_SUFFIXES or upstream.path.suffix not in _TEXT_SUFFIXES:
        identical = filecmp.cmp(target, upstream.path, shallow=False)
        return "" if identical else f"Binary files differ: {target} and {upstream.path}\n"
    local_lines = _strip_provenance_header(
        target.read_text(encoding="utf-8").splitlines(keepends=True)
    )
    upstream_lines = upstream.path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            upstream_lines,
            local_lines,
            fromfile=f"{upstream.label}:{resolution.logical_path}",
            tofile=f"project:{resolution.logical_path}",
        )
    )


def _template_override_path(docs: DocsConfig, logical_path: str) -> Path:
    if logical_path.startswith(("views/", "layouts/")) or logical_path in {
        "shell.html",
        "search.html",
    }:
        return (docs.theme_dir / logical_path).resolve()
    return (docs.theme_dir / "templates" / logical_path).resolve()


def _next_upstream(resolution: ThemeResolution, target: Path) -> ThemeCandidate | None:
    found_target = False
    for candidate in resolution.candidates:
        if candidate.path == target:
            found_target = True
            continue
        if not candidate.exists:
            continue
        if found_target:
            return candidate
        if candidate.path != target and not found_target:
            # Diff an override against the first lower-priority source.
            return candidate
    return None


def _copy_with_provenance(
    source: Path, target: Path, logical_path: str, *, source_label: str
) -> int:
    if source.is_dir():
        copied = 0
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(source)
            copied += _copy_file_with_provenance(
                path,
                target / rel,
                f"{logical_path}/{rel.as_posix()}",
                source_label=source_label,
            )
        return copied
    return _copy_file_with_provenance(source, target, logical_path, source_label=source_label)


def _copy_file_with_provenance(
    source: Path, target: Path, logical_path: str, *, source_label: str
) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix not in _TEXT_SUFFIXES:
        shutil.copy2(source, target)
        return 1
    body = source.read_text(encoding="utf-8")
    target.write_text(
        _provenance_header(source.suffix, source_label=source_label, logical_path=logical_path)
        + body,
        encoding="utf-8",
    )
    return 1


def _provenance_header(suffix: str, *, source_label: str, logical_path: str) -> str:
    source = f"{source_label}:{logical_path}"
    if suffix == ".html":
        return (
            f"{{# Ejected from {source}. "
            f"Compare with upstream via: fura theme diff {logical_path} #}}\n"
        )
    if suffix in {".css", ".js"}:
        return (
            f"/* Ejected from {source}. "
            f"Compare with upstream via: fura theme diff {logical_path} */\n"
        )
    if suffix in {".svg", ".xml"}:
        return (
            f"<!-- Ejected from {source}. "
            f"Compare with upstream via: fura theme diff {logical_path} -->\n"
        )
    return f"# Ejected from {source}. Compare with upstream via: fura theme diff {logical_path}\n"


def _strip_provenance_header(lines: list[str]) -> list[str]:
    if not lines:
        return lines
    first = lines[0]
    if "Ejected from " in first and "fura theme diff" in first:
        return lines[1:]
    return lines


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
