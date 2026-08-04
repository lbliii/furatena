"""Sharded sitemap and llms.txt discovery contracts."""

from __future__ import annotations

import base64
from html import escape
from typing import Any

from furatena.catalog.access import AccessPermission, AccessSubject
from furatena.catalog.catalog_shards import is_safe_mount_id


def discovery_mount_token(mount_id: str) -> str:
    """Return one canonical path-safe token without changing mount identity."""
    if is_safe_mount_id(mount_id):
        return mount_id
    encoded = base64.urlsafe_b64encode(mount_id.encode()).decode().rstrip("=")
    return f"~{encoded}"


def discovery_mount_id(catalog: Any, token: str) -> str | None:
    """Resolve only canonical tokens derived from configured mount identities."""
    return next(
        (mount.id for mount in catalog.mounts if discovery_mount_token(mount.id) == token),
        None,
    )


def discovery_mounts(
    catalog: Any,
    *,
    subject: AccessSubject | None = None,
) -> tuple[Any, ...]:
    """Return export-visible mounts in deterministic identity order without loading shards."""
    return tuple(
        mount
        for mount in sorted(catalog.mounts, key=lambda item: item.id)
        if catalog.can_access_mount(mount, subject, permission=AccessPermission.EXPORT)
    )


def sitemap_index_xml(
    catalog: Any,
    base_url: str = "",
    *,
    path_prefix: str = "",
    subject: AccessSubject | None = None,
) -> str:
    """Return a hub sitemap index that does not materialize catalog shards."""
    base = base_url.rstrip("/")
    prefix = path_prefix.rstrip("/")
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for mount in discovery_mounts(catalog, subject=subject):
        token = discovery_mount_token(mount.id)
        path = catalog.scoped_url(f"{prefix}/sitemaps/{token}.xml")
        loc = f"{base}{path}" if base else path
        lines.extend(("  <sitemap>", f"    <loc>{escape(loc)}</loc>", "  </sitemap>"))
    lines.append("</sitemapindex>")
    return "\n".join(lines) + "\n"


def llms_hub_txt(
    catalog: Any,
    *,
    site_name: str,
    site_description: str = "",
    path_prefix: str = "",
    subject: AccessSubject | None = None,
) -> str:
    """Return a link-only hub index over independently generated mount files."""
    summary = " ".join(site_description.split()) or f"Documentation index for {site_name}."
    prefix = path_prefix.rstrip("/")
    lines = [f"# {site_name} Documentation", "", f"> {summary}", "", "## Mount indexes", ""]
    for mount in discovery_mounts(catalog, subject=subject):
        token = discovery_mount_token(mount.id)
        path = catalog.scoped_url(f"{prefix}/llms/{token}.txt")
        lines.append(f"- [{mount.label}]({path})")
    return "\n".join(lines) + "\n"
