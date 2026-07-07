"""MDX → canonical markdown migration (Wave F)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from furatena.catalog.directives.registry import create_directive_registry
from furatena.catalog.patitas_bridge import split_frontmatter
from furatena.catalog.sources.adapters.mdx import mdx_to_markdown

_UNMIGRATED_JSX_RE = re.compile(r"<[A-Z][A-Za-z0-9_]*")


@dataclass(slots=True)
class MigrateReport:
    """Result of migrating one MDX source file."""

    source_path: Path
    target_path: Path
    changed: bool = False
    written: bool = False
    removed_source: bool = False
    unmigrated_components: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


def _format_page(meta: dict, body: str) -> str:
    body = body.strip()
    if not meta:
        return f"{body}\n" if body else "\n"
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"


def _find_unmigrated_jsx(text: str) -> tuple[str, ...]:
    known_directives = create_directive_registry().names
    components = {match.group(0)[1:] for match in _UNMIGRATED_JSX_RE.finditer(text)}
    return tuple(sorted(name for name in components if name.lower() not in known_directives))


def _parse_warnings(body: str) -> tuple[str, ...]:
    warnings: list[str] = []
    try:
        from furatena.catalog.render import DocsRenderer

        DocsRenderer().parse(body)
    except Exception as exc:
        warnings.append(f"converted body failed to parse: {exc}")
    return tuple(warnings)


def migrate_mdx_body(body: str) -> tuple[str, tuple[str, ...]]:
    """Lower JSX in a markdown body to Patitas extension blocks."""
    unmigrated = _find_unmigrated_jsx(body)
    converted = mdx_to_markdown(body)
    return converted, unmigrated


def migrate_mdx_text(source: str) -> tuple[str, MigrateReport]:
    """Convert a full MDX page (optional front matter + body)."""
    meta, body = split_frontmatter(source)
    converted_body, unmigrated = migrate_mdx_body(body)
    meta = dict(meta)
    meta.setdefault("migrated_from", "mdx")
    target_text = _format_page(meta, converted_body)
    report = MigrateReport(
        source_path=Path(""),
        target_path=Path(""),
        changed=converted_body.strip() != body.strip() or bool(unmigrated),
        unmigrated_components=unmigrated,
        warnings=_parse_warnings(converted_body),
    )
    return target_text, report


def migrate_mdx_file(
    path: Path,
    *,
    write: bool = False,
    remove_source: bool = True,
) -> MigrateReport:
    """Migrate ``path`` (.mdx) to a sibling ``.md`` file."""
    path = path.resolve()
    if path.suffix.lower() != ".mdx":
        return MigrateReport(
            source_path=path,
            target_path=path,
            errors=(f"not an MDX file: {path}",),
        )
    if not path.is_file():
        return MigrateReport(
            source_path=path,
            target_path=path.with_suffix(".md"),
            errors=(f"file not found: {path}",),
        )

    source_text = path.read_text(encoding="utf-8")
    target_path = path.with_suffix(".md")
    target_text, partial = migrate_mdx_text(source_text)
    existing = target_path.read_text(encoding="utf-8") if target_path.is_file() else ""
    changed = target_text != existing or target_text != source_text

    report = MigrateReport(
        source_path=path,
        target_path=target_path,
        changed=changed or partial.changed,
        unmigrated_components=partial.unmigrated_components,
        warnings=partial.warnings,
    )

    if write and (changed or not target_path.is_file()):
        target_path.write_text(target_text, encoding="utf-8")
        report.written = True
        if remove_source:
            path.unlink()
            report.removed_source = True

    return report


def migrate_mdx_paths(
    paths: list[Path],
    *,
    write: bool = False,
    remove_source: bool = True,
) -> list[MigrateReport]:
    reports: list[MigrateReport] = []
    for path in paths:
        reports.append(
            migrate_mdx_file(path, write=write, remove_source=remove_source)
        )
    return reports


def migrate_mounts(
    mounts,
    *,
    write: bool = False,
    remove_source: bool = True,
) -> list[MigrateReport]:
    """Scan every mount content root for ``*.mdx`` and migrate."""
    reports: list[MigrateReport] = []
    for mount in mounts:
        root = mount.content_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.mdx")):
            reports.append(
                migrate_mdx_file(path, write=write, remove_source=remove_source)
            )
    return reports
