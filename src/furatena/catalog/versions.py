"""Version channels for docs on one graph."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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
                return DocChannelTarget(
                    href=_scoped_node_url(catalog, candidate),
                    resolution="ancestor",
                    resolved_slug=str(candidate.slug),
                )

        landing_path = _mount_landing_path(catalog, mount_id, channel_id)
        if landing_path is None:
            return None
        candidate = catalog.get_path(landing_path)
        if candidate is None or not _public_candidate(catalog, candidate):
            return None
        return DocChannelTarget(
            href=_scoped_node_url(catalog, candidate),
            resolution="landing",
            resolved_slug=str(candidate.slug),
        )


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
