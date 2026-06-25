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

    return (
        DocChannel(id="latest", label="Latest", default=True),
    )


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
    if channel_id == "latest":
        return "/docs/"
    return f"/releases/{channel_id}/"


def channel_context(
    channels: tuple[DocChannel, ...],
    active_id: str,
) -> dict[str, Any]:
    """Template context for a version switcher."""
    return {
        "doc_channels": [
            {
                "id": ch.id,
                "label": ch.label,
                "active": ch.id == active_id,
                "href": channel_href(ch.id),
            }
            for ch in channels
        ],
        "active_channel": active_id,
    }
