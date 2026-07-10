"""Inspectable route contracts for registrar drift detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RouteManifestEntry:
    """One stable application-owned route contract."""

    path: str
    route_name: str | None
    methods: tuple[str, ...]
    mount: str
    handler: str
    handler_origin: str
    response_contract: str
    template: str | None
    fragment: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def snapshot_line(self) -> str:
        values = (
            ",".join(self.methods),
            self.path,
            self.route_name or "-",
            self.mount,
            self.handler,
            self.response_contract,
            self.template or "-",
            self.fragment or "-",
        )
        return "|".join(values)


_TEMPLATE_CONTRACTS = {
    "portal": "config:views.portal",
    "develop_index": "views/develop.html",
    "develop_export_preview": "views/develop_export.html",
    "home": "dynamic:view-kind",
    "default_mount_section": "dynamic:view-kind",
    "prefixed_mount": "dynamic:view-kind",
    "author_dashboard": "views/author_dashboard.html",
    "author_studio": "views/author_studio.html",
    "author_studio_save": "views/author_studio.html",
    "search": "search.html",
    "error_suggest": "partials/error_suggest_panel.html",
    "search_suggest": "partials/search_suggest.html",
}

_FRAGMENT_CONTRACTS = {
    "author_studio_save": "author_studio_workspace",
    "author_page_status": "author-page-chrome",
    "search": "search_results+oob-rails",
    "error_suggest": "error_suggest_panel",
    "search_suggest": "search_suggest",
}

_RESPONSE_CONTRACTS = {
    "author_stale": "json",
    "author_events": "event-stream",
    "author_dashboard": "html-or-json",
    "author_studio_save": "html-or-json",
    "author_page_status": "json-or-fragment",
    "author_page_source": "text-or-json",
    "author_page_transition": "form-action-or-json",
    "search": "html-or-fragment",
    "error_suggest": "html-fragment",
    "search_suggest": "html-fragment",
    "search_semantic": "json",
    "catalog_retrieve": "json",
    "og_image": "image",
    "favicon": "image",
    "inventory_inv": "binary",
}


def _handler_name(route: Any) -> str:
    return str(getattr(route.handler, "__name__", route.handler.__class__.__name__))


def _handler_origin(route: Any) -> str:
    handler = route.page_source_handler or route.handler
    module = getattr(handler, "__module__", "")
    qualname = getattr(handler, "__qualname__", _handler_name(route))
    return f"{module}:{qualname}"


def _logical_handler(route: Any) -> str:
    qualname = str(getattr(route.handler, "__qualname__", _handler_name(route)))
    return qualname.rsplit(".<locals>.", 1)[-1]


def _response_contract(path: str, handler: str) -> str:
    if handler in _RESPONSE_CONTRACTS:
        return _RESPONSE_CONTRACTS[handler]
    suffix = Path(path).suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".xml":
        return "xml"
    if suffix == ".txt":
        return "text"
    if suffix == ".md":
        return "markdown"
    if suffix == ".inv":
        return "binary"
    return "html-document"


def _route_mount(path: str, handler: str, catalog: Any | None) -> str:
    if handler.startswith("author_"):
        return "catalog"
    if handler in {"home", "default_mount_section"}:
        default_mount = getattr(catalog, "default_mount", None)
        return str(getattr(default_mount, "id", "default"))
    if handler == "prefixed_mount":
        for mount in getattr(catalog, "mounts", ()):
            prefix = str(getattr(mount, "url_prefix", "")).rstrip("/")
            if prefix and (path in {f"{prefix}/", f"{prefix}.md"} or path.startswith(f"{prefix}/")):
                return str(mount.id)
        return "mounted"
    return "application"


def route_manifest_entries(
    app: Any, *, catalog: Any | None = None
) -> tuple[RouteManifestEntry, ...]:
    """Return sorted contracts for the app's explicitly registered routes."""
    entries: list[RouteManifestEntry] = []
    for route in app._pending_routes:
        if not str(getattr(route.handler, "__module__", "")).startswith("furatena."):
            continue
        handler = _handler_name(route)
        logical_handler = _logical_handler(route)
        entries.append(
            RouteManifestEntry(
                path=str(route.path),
                route_name=route.name,
                methods=tuple(sorted(method.upper() for method in (route.methods or ("GET",)))),
                mount=_route_mount(str(route.path), logical_handler, catalog),
                handler=handler,
                handler_origin=_handler_origin(route),
                response_contract=_response_contract(str(route.path), logical_handler),
                template=route.template or _TEMPLATE_CONTRACTS.get(logical_handler),
                fragment=_FRAGMENT_CONTRACTS.get(logical_handler),
            )
        )
    return tuple(sorted(entries, key=lambda item: (item.path, item.methods, item.handler)))


def route_manifest_payload(app: Any, *, catalog: Any | None = None) -> dict[str, object]:
    """Return the stable JSON payload published by ``/routes.json``."""
    entries = route_manifest_entries(app, catalog=catalog)
    return {
        "schema_version": 1,
        "route_count": len(entries),
        "routes": [entry.to_dict() for entry in entries],
    }
