"""Mike-compatible edition discovery artifacts with mount-safe hub composition."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from furatena.catalog.access import AccessPermission, AccessSubject
from furatena.catalog.catalog_shards import is_safe_mount_id
from furatena.catalog.edition_routing import edition_path
from furatena.catalog.packaging import normalize_base_path


def versions_mount_url(mount_id: str) -> str | None:
    """Return the public Mike-array URL for one safe mount id."""
    if not is_safe_mount_id(mount_id):
        return None
    return f"/versions/mounts/{mount_id}.json"


def versions_mount_path(mount_id: str) -> Path | None:
    """Return the static path for one safe per-mount versions artifact."""
    if not is_safe_mount_id(mount_id):
        return None
    return Path("versions") / "mounts" / f"{mount_id}.json"


def public_versioned_mounts(catalog: Any) -> tuple[Any, ...]:
    """Return versioned mounts visible to anonymous export consumers."""
    visible = (
        mount
        for mount in getattr(catalog, "mounts", ())
        if getattr(mount, "editions", None) is not None
        and catalog.can_access_mount(
            mount.id,
            AccessSubject.anonymous(),
            permission=AccessPermission.EXPORT,
        )
    )
    return tuple(sorted(visible, key=lambda mount: str(mount.id)))


def versions_for_mount(
    catalog: Any,
    mount_id: str,
    *,
    base_url: str = "",
    base_path: str = "",
) -> list[dict[str, Any]]:
    """Return one Mike-compatible edition array for a public versioned mount."""
    mount = next(
        (item for item in public_versioned_mounts(catalog) if item.id == mount_id),
        None,
    )
    if mount is None:
        raise KeyError(mount_id)

    aliases_by_edition: dict[str, list[str]] = {}
    for alias, edition in catalog.edition_aliases_for(mount_id).items():
        aliases_by_edition.setdefault(edition, []).append(alias)

    prefix = _deployment_base_path(base_url=base_url, base_path=base_path)
    mount_prefix = mount.url_prefix or "/"
    edition_ids = sorted(
        catalog.edition_ids_for(mount_id),
        key=lambda edition: edition != "latest",
    )
    return [
        {
            "version": edition,
            "title": "Latest" if edition == "latest" else edition,
            "aliases": sorted(aliases_by_edition.get(edition, ())),
            "url_prefix": _prefixed_edition_path(prefix, mount_prefix, edition),
        }
        for edition in edition_ids
    ]


def versions_manifest(
    catalog: Any,
    *,
    base_url: str = "",
    base_path: str = "",
) -> dict[str, Any]:
    """Return the mount-keyed hub edition manifest."""
    return {
        "schema_version": 1,
        "mounts": {
            mount.id: versions_for_mount(
                catalog,
                mount.id,
                base_url=base_url,
                base_path=base_path,
            )
            for mount in public_versioned_mounts(catalog)
        },
    }


def _deployment_base_path(*, base_url: str, base_path: str) -> str:
    explicit = normalize_base_path(base_path)
    if explicit:
        return explicit
    return normalize_base_path(urlsplit(base_url).path)


def _prefixed_edition_path(base_path: str, mount_prefix: str, edition: str) -> str:
    scoped = edition_path(mount_prefix, edition).rstrip("/") or "/"
    if not base_path:
        return scoped
    if scoped == "/":
        return base_path
    return f"{base_path}{scoped}"
