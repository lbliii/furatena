"""Version channels for docs on one graph."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import yaml

from furatena.catalog.graph_schema import parse_node_id

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


@dataclass(frozen=True, slots=True)
class DocChannel:
    """One selectable docs version on the shared catalog graph."""

    id: str
    label: str
    default: bool = False


@dataclass(frozen=True, slots=True)
class DocChannelTarget:
    """Resolved switch target for one edition of the current mount."""

    href: str
    resolution: str
    resolved_slug: str


def active_channel_id() -> str:
    """Active channel from ``FURA_CHANNEL`` (default ``latest``)."""
    return os.environ.get("FURA_CHANNEL", "latest").strip() or "latest"


def load_channels(config_path: Path | None = None) -> tuple[DocChannel, ...]:
    """Load version channels from yaml or infer from release notes."""
    if config_path and config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        channels_raw = raw.get("channels") or raw.get("versions") or []
        channels: list[DocChannel] = []
        for item in channels_raw:
            if not isinstance(item, dict):
                continue
            channel_id = str(item.get("id") or item.get("version") or "").strip()
            if not channel_id:
                continue
            channels.append(
                DocChannel(
                    id=channel_id,
                    label=str(item.get("label") or channel_id),
                    default=bool(item.get("default")),
                )
            )
        if channels:
            if not any(ch.default for ch in channels):
                return tuple(channels)
            return tuple(channels)

    return (DocChannel(id="latest", label="Latest", default=True),)


def infer_release_channels(content_root: Path) -> tuple[DocChannel, ...]:
    """Build version channels from ``releases/*.md`` semver filenames."""
    releases_dir = content_root / "releases"
    if not releases_dir.is_dir():
        return load_channels()

    semver_files: list[tuple[tuple[int, ...], str]] = []
    for path in releases_dir.glob("*.md"):
        if path.name == "_index.md":
            continue
        version = path.stem
        if not _VERSION_RE.match(version):
            continue
        parts = tuple(int(p) for p in version.split("."))
        semver_files.append((parts, version))

    semver_files.sort(reverse=True)
    channels = [DocChannel(id="latest", label="Latest", default=True)]
    for _, version in semver_files[:5]:
        channels.append(DocChannel(id=version, label=f"v{version}"))
    return tuple(channels)


def node_matches_channel(node_version: str | None, channel_id: str) -> bool:
    """Return whether a node belongs on the active channel."""
    if channel_id == "latest":
        return node_version in (None, "", "latest")
    return node_version == channel_id


def channel_href(channel_id: str) -> str:
    """URL for switching to a docs version channel."""
    from furatena.catalog.edition_routing import edition_path

    return edition_path("/docs/", channel_id)


def _channel_status(catalog: Any, mount_id: str, channel_id: str) -> str:
    if channel_id == "latest":
        return "current"
    discovered = getattr(catalog, "discovered_editions_for", None)
    if not callable(discovered):
        return ""
    snapshot = next(
        (item for item in discovered(mount_id) if getattr(item, "id", "") == channel_id),
        None,
    )
    return str(getattr(snapshot, "status", "") or "")


def _public_candidate(catalog: Any, node: Any) -> bool:
    can_access = getattr(catalog, "can_access_node", None)
    return not callable(can_access) or bool(can_access(node))


def _scoped_node_url(catalog: Any, node: Any) -> str:
    scoped_url = getattr(catalog, "scoped_url", None)
    return scoped_url(node.url) if callable(scoped_url) else str(node.url)


def _mount_landing_path(catalog: Any, mount_id: str, channel_id: str) -> str | None:
    from furatena.catalog.edition_routing import edition_path

    mount = next(
        (item for item in getattr(catalog, "mounts", ()) if getattr(item, "id", "") == mount_id),
        None,
    )
    if mount is None:
        return None
    return edition_path(str(getattr(mount, "url_prefix", "") or "/"), channel_id)


def resolve_channel_target(
    catalog: Any,
    node: Any,
    channel_id: str,
) -> DocChannelTarget | None:
    """Resolve a page within a sibling edition without crossing mount boundaries."""
    mount_id = str(node.mount)
    if (
        channel_id != catalog.active_channel
        and _channel_status(catalog, mount_id, channel_id) == "eol"
    ):
        return None

    if channel_id == catalog.active_channel:
        return DocChannelTarget(
            href=_scoped_node_url(catalog, node),
            resolution="direct",
            resolved_slug=str(node.slug),
        )

    projection_resolver = getattr(catalog, "resolve_edition_page", None)
    if callable(projection_resolver):
        resolved = projection_resolver(node, channel_id)
        if resolved is None:
            return None
        from furatena.catalog.edition_projection import projection_page_url

        target = DocChannelTarget(
            href=catalog.scoped_url(projection_page_url(resolved.page)),
            resolution=resolved.resolution,
            resolved_slug=resolved.page.slug,
        )
        return _fallback_target(
            target,
            source_node_id=_source_node_id(catalog, node),
            source_slug=str(node.slug),
            channel_id=channel_id,
        )

    with catalog.use_edition(channel_id):
        candidate = catalog.get_by_slug(node.slug, mount=mount_id)
        if candidate is not None and _public_candidate(catalog, candidate):
            return DocChannelTarget(
                href=_scoped_node_url(catalog, candidate),
                resolution="direct",
                resolved_slug=str(candidate.slug),
            )

        parts = [part for part in str(node.slug).strip("/").split("/") if part]
        for end in range(len(parts) - 1, 0, -1):
            ancestor_slug = "/".join(parts[:end])
            candidate = catalog.get_by_slug(ancestor_slug, mount=mount_id)
            if candidate is not None and _public_candidate(catalog, candidate):
                return _fallback_target(
                    DocChannelTarget(
                        href=_scoped_node_url(catalog, candidate),
                        resolution="ancestor",
                        resolved_slug=str(candidate.slug),
                    ),
                    source_node_id=_source_node_id(catalog, node),
                    source_slug=str(node.slug),
                    channel_id=channel_id,
                )

        landing_path = _mount_landing_path(catalog, mount_id, channel_id)
        if landing_path is None:
            return None
        candidate = catalog.get_path(landing_path)
        if candidate is None or not _public_candidate(catalog, candidate):
            return None
        return _fallback_target(
            DocChannelTarget(
                href=_scoped_node_url(catalog, candidate),
                resolution="landing",
                resolved_slug=str(candidate.slug),
            ),
            source_node_id=_source_node_id(catalog, node),
            source_slug=str(node.slug),
            channel_id=channel_id,
        )


def _fallback_target(
    target: DocChannelTarget,
    *,
    source_node_id: str,
    source_slug: str,
    channel_id: str,
) -> DocChannelTarget:
    if target.resolution not in {"ancestor", "landing"}:
        return target
    separator = "&" if "?" in target.href else "?"
    query = urlencode(
        {
            "version_fallback": target.resolution,
            "version_from": source_slug,
            "version_source": source_node_id,
            "version_target": channel_id,
        }
    )
    return DocChannelTarget(
        href=f"{target.href}{separator}{query}",
        resolution=target.resolution,
        resolved_slug=target.resolved_slug,
    )


def _source_node_id(catalog: Any, node: Any) -> str:
    existing = str(getattr(node, "node_id", "") or "").strip()
    if existing:
        return existing
    mount = str(node.mount)
    edition = str(getattr(node, "edition", "") or catalog.active_channel)
    slug = str(node.slug).strip("/") or "index"
    return f"{mount}:{edition}:{slug}"


def version_fallback_context(
    catalog: Any,
    node: Any,
    request: Any | None,
) -> dict[str, Any]:
    """Render a truthful notice only for a validated ancestor/landing fallback."""
    query = getattr(request, "query", None)
    if query is None:
        return {"version_fallback_notice": None}
    resolution = str(query.get("version_fallback") or "").strip()
    source_slug = str(query.get("version_from") or "").strip().strip("/")
    source_node_id = str(query.get("version_source") or "").strip()
    target_edition = str(query.get("version_target") or "").strip()
    if (
        resolution not in {"ancestor", "landing"}
        or not source_slug
        or target_edition != str(node.edition)
        or target_edition != str(catalog.active_channel)
    ):
        return {"version_fallback_notice": None}
    try:
        source_mount, _source_edition, source_id_slug = parse_node_id(source_node_id)
    except ValueError:
        return {"version_fallback_notice": None}
    normalized_id_slug = "" if source_id_slug == "index" else source_id_slug
    if source_mount != str(node.mount) or normalized_id_slug != source_slug:
        return {"version_fallback_notice": None}
    projection_method = getattr(catalog, "edition_projection", None)
    if not callable(projection_method):
        return {"version_fallback_notice": None}
    resolved = projection_method().resolve(
        mount=str(node.mount),
        source_node_id=source_node_id,
        target_edition=target_edition,
    )
    if (
        resolved is None
        or resolved.resolution != resolution
        or resolved.page.node_id != str(node.node_id)
    ):
        return {"version_fallback_notice": None}
    destination = (
        "the nearest available ancestor" if resolution == "ancestor" else "the edition landing page"
    )
    label = "Latest" if target_edition == "latest" else f"v{target_edition}"
    return {
        "version_fallback_notice": {
            "resolution": resolution,
            "source_slug": source_slug,
            "target_edition": target_edition,
            "message": (
                f"The requested page {source_slug!r} is not available in {label}. "
                f"Showing {destination}."
            ),
        }
    }


def channel_context(
    channels: tuple[DocChannel, ...],
    active_id: str,
    *,
    catalog: Any | None = None,
    node: Any | None = None,
) -> dict[str, Any]:
    """Template context for a version switcher."""
    channel_records: list[dict[str, Any]] = []
    for channel in channels:
        target = (
            resolve_channel_target(catalog, node, channel.id)
            if catalog is not None and node is not None
            else DocChannelTarget(
                href=channel_href(channel.id),
                resolution="landing",
                resolved_slug="",
            )
        )
        if target is None:
            continue
        channel_records.append(
            {
                "id": channel.id,
                "label": channel.label,
                "active": channel.id == active_id,
                "href": target.href,
                "resolution": target.resolution,
                "resolved_slug": target.resolved_slug,
            }
        )
    return {
        "doc_channels": channel_records,
        "active_channel": active_id,
    }


def edition_banner_context(catalog: Any, node: Any) -> dict[str, Any]:
    """Build one lifecycle banner using the shared cross-edition resolver."""
    lifecycle_for = getattr(catalog, "edition_lifecycle_for", None)
    if not callable(lifecycle_for):
        return {"edition_banner": None}
    lifecycle = lifecycle_for(str(node.mount), str(node.edition))
    if lifecycle.status == "current":
        return {"edition_banner": None}
    target = resolve_channel_target(catalog, node, "latest")
    labels = {
        "legacy": "This page documents a legacy edition.",
        "deprecated": "This documentation edition is deprecated.",
        "preview": "This page documents a preview edition.",
        "eol": "This documentation edition has reached end of life.",
    }
    return {
        "edition_banner": {
            "status": lifecycle.status,
            "message": lifecycle.banner or labels[lifecycle.status],
            "current_href": target.href if target is not None else None,
            "resolution": target.resolution if target is not None else None,
            "end_of_life": lifecycle.end_of_life,
        }
    }
