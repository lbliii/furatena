"""Tenant/workspace/site identity helpers for routes and artifacts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

DEFAULT_IDENTITY = {
    "tenant": "default",
    "workspace": "default",
    "site": "default",
}

_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def normalize_identity(identity: Mapping[str, object] | None) -> dict[str, str]:
    """Return tenant/workspace/site values with backwards-compatible defaults."""
    raw = identity or {}
    return {
        key: str(raw.get(key) or default).strip() or default
        for key, default in DEFAULT_IDENTITY.items()
    }


def safe_identity_component(value: object) -> str:
    """Return a component safe for filesystem paths and URL segments."""
    text = str(value or "default").strip().lower()
    text = _SAFE_COMPONENT_RE.sub("-", text)
    text = text.strip(".-_")
    return text or "default"


def identity_namespace_parts(identity: Mapping[str, object] | None) -> tuple[str, ...]:
    """Return stable namespace path components for a catalog identity."""
    meta = normalize_identity(identity)
    return (
        "tenants",
        safe_identity_component(meta["tenant"]),
        "workspaces",
        safe_identity_component(meta["workspace"]),
        "sites",
        safe_identity_component(meta["site"]),
    )


def is_default_identity(identity: Mapping[str, object] | None) -> bool:
    """True when the identity is the historical single-site default."""
    meta = normalize_identity(identity)
    return all(meta[key] == value for key, value in DEFAULT_IDENTITY.items())


def scoped_frozen_dir(base: Path, identity: Mapping[str, object] | None) -> Path:
    """Return the frozen root for an identity, preserving legacy default paths."""
    if is_default_identity(identity):
        return base
    return base.joinpath(*identity_namespace_parts(identity))


def identity_route_prefix(identity: Mapping[str, object] | None) -> str:
    """Return a tenant/workspace/site route prefix, or empty for default identity."""
    if is_default_identity(identity):
        return ""
    return "/" + "/".join(identity_namespace_parts(identity))


def strip_identity_route(path: str, identity: Mapping[str, object] | None) -> str:
    """Strip this catalog's identity route prefix from a request path when present."""
    prefix = identity_route_prefix(identity)
    if not prefix:
        return path
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == prefix:
        return "/"
    prefix_slash = f"{prefix}/"
    if normalized.startswith(prefix_slash):
        stripped = normalized.removeprefix(prefix)
        return stripped or "/"
    return path


def scope_url(path: str, identity: Mapping[str, object] | None) -> str:
    """Prefix a root-relative catalog URL with this identity's route namespace."""
    prefix = identity_route_prefix(identity)
    if not prefix:
        return path
    normalized = path if path.startswith("/") else f"/{path}"
    if normalized == "/":
        return f"{prefix}/"
    return f"{prefix}{normalized}"
