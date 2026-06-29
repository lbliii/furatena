"""Publication lifecycle validation for source-backed docs."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ALLOWED_VISIBILITY = frozenset({"public", "private", "internal", "draft", "unlisted", "archived"})
_DATE_FIELDS = ("published_at", "updated_at", "expires_at", "archived_at")
_MD_LINK_RE = re.compile(r"\]\((/[^)#?]+)(?:[)#?][^)]*)?\)")
_WIKILINK_RE = re.compile(r"\[\[(?:([^:\]|]+):)?([^|\]]+)(?:\|[^\]]+)?\]\]")


@dataclass(frozen=True, slots=True)
class SourceLifecycleRecord:
    """Lifecycle-relevant metadata for one source file."""

    path: Path
    source_path: str
    url: str
    slug: str
    mount: str
    meta: dict[str, Any]
    body: str


def check_lifecycle_sources(catalog: Any) -> tuple[list[str], list[str]]:
    """Validate lifecycle front matter and draft/private reachability."""
    records = _collect_lifecycle_records(catalog)
    errors: list[str] = []
    warnings: list[str] = []

    for record in records:
        record_errors, record_warnings = lint_lifecycle_record(record)
        errors.extend(record_errors)
        warnings.extend(record_warnings)

    errors.extend(_check_private_target_links(records))
    return sorted(errors), sorted(warnings)


def visibility_state(meta: dict[str, Any]) -> str:
    """Return normalized lifecycle visibility for front matter."""
    return _visibility(meta)


def is_public_meta(meta: dict[str, Any]) -> bool:
    """Return whether front matter is safe for public output surfaces."""
    return not _is_private(meta)


def is_public_node(node: Any) -> bool:
    """Return whether a catalog node is safe for public output surfaces."""
    return is_public_meta(getattr(node, "meta", {}) or {})


def public_nodes(nodes: Any) -> list[Any]:
    """Filter catalog nodes down to public lifecycle state."""
    return [node for node in nodes if is_public_node(node)]


def lint_lifecycle_record(record: SourceLifecycleRecord) -> tuple[list[str], list[str]]:
    """Validate lifecycle field combinations for one source record."""
    errors: list[str] = []
    warnings: list[str] = []
    source = record.source_path
    meta = record.meta
    visibility = _visibility(meta)
    draft = _truthy(meta.get("draft"))
    archived = bool(meta.get("archived_at")) or visibility == "archived"

    raw_visibility = meta.get("visibility")
    if raw_visibility is not None and visibility not in _ALLOWED_VISIBILITY:
        allowed = ", ".join(sorted(_ALLOWED_VISIBILITY))
        errors.append(f"{source}: visibility must be one of {allowed}")

    for field in _DATE_FIELDS:
        if field in meta and meta.get(field) not in (None, "") and not _valid_datetime(meta.get(field)):
            errors.append(f"{source}: {field} must be an ISO date or datetime")

    if draft and visibility == "public":
        errors.append(f"{source}: draft pages cannot use visibility: public")
    if draft and meta.get("published_at"):
        errors.append(f"{source}: draft pages cannot set published_at")
    if visibility in {"private", "internal", "draft", "unlisted"} and meta.get("published_at"):
        errors.append(f"{source}: {visibility} pages cannot set published_at")
    if archived and visibility == "public":
        errors.append(f"{source}: archived pages cannot use visibility: public")

    if _explicit_public(meta) and not meta.get("published_at"):
        warnings.append(f"{source}: public lifecycle pages should set published_at")
    if "owner" in meta and not str(meta.get("owner") or "").strip():
        warnings.append(f"{source}: owner must not be empty when provided")
    reviewers = meta.get("reviewers")
    if reviewers is not None and not isinstance(reviewers, (list, tuple, str)):
        errors.append(f"{source}: reviewers must be a string or list")

    return errors, warnings


def _collect_lifecycle_records(catalog: Any) -> list[SourceLifecycleRecord]:
    mounts = getattr(catalog, "mounts", None)
    if mounts:
        records: list[SourceLifecycleRecord] = []
        for mount in mounts:
            records.extend(
                _scan_root(
                    mount.content_root,
                    source_config=mount.source,
                    mount_id=mount.id,
                    url_prefix=mount.url_prefix,
                )
            )
        return records

    content_root = getattr(catalog, "content_root", None)
    source_config = getattr(catalog, "source_config", None)
    mount = getattr(catalog, "mount", "chirp")
    url_prefix = getattr(catalog, "url_prefix", "")
    if content_root is None or source_config is None:
        return []
    return _scan_root(content_root, source_config=source_config, mount_id=mount, url_prefix=url_prefix)


def _scan_root(
    content_root: Path,
    *,
    source_config: Any,
    mount_id: str,
    url_prefix: str = "",
) -> list[SourceLifecycleRecord]:
    from furatena.catalog.sources.parse import parse_source_text
    from furatena.catalog.sources.scanner import file_to_url

    if not content_root.is_dir():
        return []
    files: set[Path] = set()
    for ext in source_config.tracked_extensions():
        files.update(path.resolve() for path in content_root.rglob(f"*{ext}"))

    records: list[SourceLifecycleRecord] = []
    for path in sorted(files, key=lambda item: str(item)):
        source = path.read_text(encoding="utf-8")
        content_format = source_config.content_format_for(path)
        meta, body = parse_source_text(source, content_format=content_format)
        url, slug = file_to_url(
            content_root,
            path,
            url_prefix=url_prefix,
            index_files=source_config.index_files,
        )
        records.append(
            SourceLifecycleRecord(
                path=path,
                source_path=str(path.relative_to(content_root)),
                url=url,
                slug=slug,
                mount=mount_id,
                meta=dict(meta),
                body=body,
            )
        )
    return records


def _check_private_target_links(records: list[SourceLifecycleRecord]) -> list[str]:
    private_by_url = {
        _normalize_url(record.url): record
        for record in records
        if _is_private(record.meta)
    }
    if not private_by_url:
        return []
    slug_to_url = {record.slug.strip("/"): _normalize_url(record.url) for record in records}
    errors: list[str] = []
    for record in records:
        if _is_private(record.meta):
            continue
        for href in _source_links(record.body, slug_to_url):
            target = private_by_url.get(_normalize_url(href))
            if target is None:
                continue
            errors.append(
                f"{record.source_path}: public page links to draft/private target "
                f"{target.source_path} ({target.url})"
            )
    return sorted(set(errors))


def _source_links(body: str, slug_to_url: dict[str, str]) -> set[str]:
    links = {_normalize_url(match.group(1)) for match in _MD_LINK_RE.finditer(body)}
    for match in _WIKILINK_RE.finditer(body):
        mount = match.group(1)
        if mount:
            continue
        raw = match.group(2).strip("/")
        candidates = [raw]
        if not raw.startswith("docs/"):
            candidates.append(f"docs/{raw}")
        for candidate in candidates:
            if candidate in slug_to_url:
                links.add(slug_to_url[candidate])
                break
    return links


def _visibility(meta: dict[str, Any]) -> str:
    raw = str(meta.get("visibility") or "").strip().lower()
    if raw:
        return raw
    if _truthy(meta.get("draft")):
        return "draft"
    if meta.get("archived_at"):
        return "archived"
    return "public"


def _explicit_public(meta: dict[str, Any]) -> bool:
    visibility = str(meta.get("visibility") or "").strip().lower()
    return visibility == "public" or bool(meta.get("published_at"))


def _is_private(meta: dict[str, Any]) -> bool:
    return _visibility(meta) in {"private", "internal", "draft", "unlisted", "archived"}


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_url(url: str) -> str:
    if not url.startswith("/"):
        url = f"/{url}"
    return url if url == "/" or url.endswith("/") else f"{url}/"


def _valid_datetime(value: Any) -> bool:
    if isinstance(value, (dt.date, dt.datetime)):
        return True
    text = str(value).strip()
    if not text:
        return False
    try:
        dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return True
    except ValueError:
        try:
            dt.date.fromisoformat(text)
        except ValueError:
            return False
    return True
