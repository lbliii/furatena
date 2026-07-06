"""Static site export — link frozen catalog IR into deployable HTML."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from furatena.catalog.docs_app import DocsApp

from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.identity import scoped_frozen_dir

_ROOT_PATH_ATTRS = (
    "href",
    "src",
    "action",
    "content",
    "hx-get",
    "hx-post",
    "hx-push-url",
    "formaction",
)
_ROOT_PATH_RE = re.compile(
    rf"""(?P<prefix>\s(?:{'|'.join(_ROOT_PATH_ATTRS)})=(["']))(?P<url>/[^"']*)""",
    re.IGNORECASE,
)
_JSON_URL_RE = re.compile(r'("url"\s*:\s*")(/[^"]*)(")')


@dataclass(frozen=True, slots=True)
class StaticExportResult:
    """Summary of a completed static export."""

    output_dir: Path
    page_count: int
    sidecar_count: int
    asset_mounts: int
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class StaticExportOptions:
    """Options for ``export_static_site``."""

    output_dir: Path
    base_path: str = ""
    site_url: str | None = None
    frozen_dir: Path | None = None
    include_index_txt: bool = True
    include_portal: bool = True
    include_search: bool = True
    incremental: bool = False
    extra_routes: tuple[str, ...] = ()
    allow_lifecycle_errors: bool = False


class StaticExportLifecycleError(RuntimeError):
    """Raised when public export would violate lifecycle safety checks."""

    def __init__(self, errors: list[str], warnings: list[str]) -> None:
        super().__init__("static export blocked by lifecycle safety checks")
        self.errors = errors
        self.warnings = warnings


def docs_base_path() -> str:
    """Path prefix for static hosting (e.g. ``/chirp`` on GitHub Pages project sites)."""
    configured = os.environ.get("FURA_BASE_PATH", "").strip()
    if configured:
        normalized = configured.rstrip("/")
        return "" if normalized in {"", "/"} else normalized
    site_url = os.environ.get("FURA_BASE_URL", "").strip()
    if site_url:
        path = urlparse(site_url).path.rstrip("/")
        return path if path else ""
    return ""


def normalize_base_path(base_path: str) -> str:
    """Return a leading-slash path without trailing slash, or empty for site root."""
    raw = base_path.strip()
    if not raw or raw == "/":
        return ""
    return "/" + raw.strip("/").rstrip("/")


def url_path_to_output_file(url_path: str) -> Path:
    """Map a request path to a relative output file (``index.html`` for directories)."""
    path = url_path.split("?", 1)[0]
    if not path.startswith("/"):
        path = f"/{path}"
    path = path.rstrip("/")
    if not path:
        return Path("index.html")
    if path.endswith((".txt", ".xml", ".json")):
        return Path(path.lstrip("/"))
    return Path(path.lstrip("/")) / "index.html"


def prefix_root_paths(text: str, base_path: str) -> str:
    """Prefix root-relative URLs in HTML or JSON sidecars."""
    normalized = normalize_base_path(base_path)
    if not normalized:
        return text

    def html_repl(match: re.Match[str]) -> str:
        url = match.group("url")
        if url.startswith("//"):
            return match.group(0)
        return f"{match.group('prefix')}{normalized}{url}"

    def json_repl(match: re.Match[str]) -> str:
        url = match.group(2)
        if url.startswith("//"):
            return match.group(0)
        return f'{match.group(1)}{normalized}{url}{match.group(3)}'

    text = _ROOT_PATH_RE.sub(html_repl, text)
    return _JSON_URL_RE.sub(json_repl, text)


def prefix_markdown_links(text: str, base_path: str) -> str:
    """Prefix root-relative markdown links (``(/path)``) for ``llms.txt``."""
    normalized = normalize_base_path(base_path)
    if not normalized:
        return text
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = line.lstrip()[:3]
        if marker in {"```", "~~~"}:
            fence = None if fence == marker else marker
            lines.append(line)
        elif fence is None:
            parts = re.split(r"(`+[^`]*`+)", line)
            lines.append(
                "".join(
                    part
                    if index % 2
                    else re.sub(r"\]\((/[^)]*)\)", rf"]({normalized}\1)", part)
                    for index, part in enumerate(parts)
                )
            )
        else:
            lines.append(line)
    return "".join(lines)


def _content_digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _load_manifest_fingerprints(output_dir: Path) -> dict[str, str]:
    path = output_dir / "export.manifest.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    fingerprints = payload.get("fingerprints")
    if isinstance(fingerprints, dict):
        return {str(key): str(value) for key, value in fingerprints.items()}
    return {}


def _node_source_fingerprint(node, *, renderer_fp: str, frozen_dir: Path | None) -> str:
    parts = [renderer_fp, node.slug, node.title, node.description]
    html_ref = getattr(node, "html_path", None)
    if html_ref and frozen_dir is not None:
        for base in (frozen_dir / "mounts" / node.mount / "pages", frozen_dir / "pages"):
            path = base / str(html_ref)
            if path.is_file():
                stat = path.stat()
                parts.extend((str(stat.st_mtime_ns), str(stat.st_size)))
                break
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def _route_fingerprint(docs_app: DocsApp, url_path: str, *, renderer_fp: str, frozen_dir: Path | None) -> str:
    if url_path in {"/search", "/search/"}:
        return hashlib.sha256(f"search|{renderer_fp}|{len(docs_app.catalog.nodes)}".encode()).hexdigest()[:16]
    if url_path == "/portal/":
        return hashlib.sha256(f"portal|{renderer_fp}|{len(docs_app.catalog.mounts)}".encode()).hexdigest()[:16]
    node = docs_app.catalog.get(url_path) or docs_app.catalog.get(url_path.rstrip("/") + "/")
    if node is None:
        return hashlib.sha256(f"route|{renderer_fp}|{url_path}".encode()).hexdigest()[:16]
    return _node_source_fingerprint(node, renderer_fp=renderer_fp, frozen_dir=frozen_dir)


def _sidecar_fingerprint(name: str, *, renderer_fp: str, frozen_dir: Path | None) -> str:
    if frozen_dir is not None:
        path = frozen_dir / name
        if path.is_file():
            stat = path.stat()
            return hashlib.sha256(f"{name}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()[:16]
    return hashlib.sha256(f"sidecar|{renderer_fp}|{name}".encode()).hexdigest()[:16]


def _configure_export_env(*, site_url: str | None, base_path: str) -> dict[str, str | None]:
    """Set env vars for canonical/OG URLs during render; return prior values."""
    prior: dict[str, str | None] = {
        "FURA_BASE_URL": os.environ.get("FURA_BASE_URL"),
        "FURA_BASE_PATH": os.environ.get("FURA_BASE_PATH"),
        "FURA_STATIC": os.environ.get("FURA_STATIC"),
    }
    if site_url:
        os.environ["FURA_BASE_URL"] = site_url.rstrip("/")
    if base_path:
        os.environ["FURA_BASE_PATH"] = normalize_base_path(base_path)
    os.environ["FURA_STATIC"] = "1"
    return prior


def _restore_export_env(prior: dict[str, str | None]) -> None:
    for key, value in prior.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _response_header(headers: tuple[tuple[str, str], ...], name: str, default: str = "") -> str:
    key = name.lower()
    for header_name, value in headers:
        if header_name.lower() == key:
            return value
    return default


def _default_content_type(url_path: str) -> str:
    suffix = Path(url_path.split("?", 1)[0]).suffix
    return {
        ".html": "text/html",
        ".json": "application/json",
        ".txt": "text/plain",
        ".xml": "application/xml",
    }.get(suffix, "application/octet-stream")


def _finalize_static_html(body: str, base_path: str) -> str:
    body = prefix_root_paths(body, base_path)
    if 'data-fura-static' not in body:
        body = body.replace("<body", '<body data-fura-static="true"', 1)
    prefix = normalize_base_path(base_path)
    search_url = f"{prefix}/search.json" if prefix else "/search.json"
    script_src = f"{prefix}/docs-theme/local/js/fura-static-search.js" if prefix else "/docs-theme/local/js/fura-static-search.js"
    inject = (
        f'<script>window.FURA_STATIC={{basePath:{json.dumps(prefix)},'
        f'searchUrl:{json.dumps(search_url)}}};</script>\n'
        f'<script src="{script_src}" defer></script>\n'
    )
    if inject not in body:
        body = body.replace("</head>", inject + "</head>", 1)
    return body


def _prepare_body(
    body: str,
    *,
    content_type: str,
    base_path: str,
    rel: Path,
) -> str:
    if "html" in content_type:
        return _finalize_static_html(body, base_path)
    if "json" in content_type:
        return prefix_root_paths(body, base_path)
    if "text/plain" in content_type and rel.suffix == ".txt":
        return prefix_markdown_links(body, base_path)
    return body


def _write_response(
    output_dir: Path,
    url_path: str,
    *,
    body: str,
    content_type: str,
    base_path: str,
) -> Path:
    rel = url_path_to_output_file(url_path)
    target = output_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    prepared = _prepare_body(body, content_type=content_type, base_path=base_path, rel=rel)
    target.write_text(prepared, encoding="utf-8")
    return target


def _maybe_skip_existing(
    output_dir: Path,
    url_path: str,
    *,
    body: str,
    content_type: str,
    base_path: str,
    incremental: bool,
) -> bool:
    if not incremental:
        return False
    rel = url_path_to_output_file(url_path)
    target = output_dir / rel
    if not target.is_file():
        return False
    prepared = _prepare_body(body, content_type=content_type, base_path=base_path, rel=rel)
    return _content_digest(target.read_text(encoding="utf-8")) == _content_digest(prepared)


def _copy_static_mount(src_dir: Path, url_prefix: str, output_dir: Path) -> None:
    rel = url_prefix.lstrip("/")
    if not rel:
        return
    dest = output_dir / rel
    if not src_dir.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for path in src_dir.rglob("*"):
        if not path.is_file():
            continue
        target = dest / path.relative_to(src_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or path.stat().st_mtime > target.stat().st_mtime:
            shutil.copy2(path, target)


def _copy_chirp_ui_static(output_dir: Path) -> None:
    try:
        import chirp_ui
    except ImportError:
        return
    static_path = chirp_ui.static_path()
    if not static_path.is_dir():
        return
    dest = output_dir / "static"
    dest.mkdir(parents=True, exist_ok=True)
    for path in static_path.rglob("*"):
        if not path.is_file():
            continue
        target = dest / path.relative_to(static_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or path.stat().st_mtime > target.stat().st_mtime:
            shutil.copy2(path, target)


def _copy_theme_assets(docs_app: DocsApp, output_dir: Path) -> int:
    count = 0
    for mount in docs_app.theme.static_mounts:
        _copy_static_mount(mount.directory, mount.url_prefix, output_dir)
        count += 1
    _copy_chirp_ui_static(output_dir)
    return count + 1


def _copy_frozen_sidecar(frozen_dir: Path | None, name: str, output_dir: Path, base_path: str) -> bool:
    if frozen_dir is None:
        return False
    source = frozen_dir / name
    if not source.is_file():
        return False
    body = source.read_text(encoding="utf-8")
    if name.endswith(".json"):
        body = prefix_root_paths(body, base_path)
    (output_dir / name).write_text(body, encoding="utf-8")
    return True


def _canonical_export_path(url_path: str) -> str:
    """Map catalog URLs to routable export paths."""
    if url_path in {"/index/", "/index"}:
        return "/"
    return url_path


def _collect_routes(docs_app: DocsApp, options: StaticExportOptions) -> list[str]:
    from furatena.catalog.access import AccessPermission, accessible_nodes
    from furatena.catalog.i18n import collect_i18n_export_routes, collect_i18n_home_routes

    public_nodes = accessible_nodes(
        docs_app.catalog,
        docs_app.catalog.nodes,
        permission=AccessPermission.EXPORT,
    )
    public_urls = {node.url for node in public_nodes}
    routes = {
        docs_app.catalog.scoped_url(_canonical_export_path(url))
        for url in public_urls
    }
    i18n = docs_app.config.i18n
    if i18n.enabled:
        routes.update(collect_i18n_home_routes(i18n))
        routes.update(
            docs_app.catalog.scoped_url(url)
            for url in collect_i18n_export_routes(docs_app.catalog, i18n)
            if url in public_urls
        )
    if options.include_portal and "/portal/" not in routes:
        routes.add("/portal/")
    if options.include_search:
        for path in ("/search", "/search/"):
            routes.add(path)
    from furatena.catalog.develop_exports import DEVELOP_EXPORTS

    routes.add("/develop/")
    routes.update(item.preview_href for item in DEVELOP_EXPORTS)
    for path in options.extra_routes:
        routes.add(path)
    return sorted(routes)


def _sidecar_routes() -> tuple[str, ...]:
    return (
        "/catalog.json",
        "/catalog/api-operations.json",
        "/search.json",
        "/tools.json",
        "/sitemap.xml",
        "/llms.txt",
        "/llms-full.txt",
        "/meta.json",
        "/surface.json",
        "/channels.json",
        "/deployment-profiles.json",
        "/inventories.json",
    )


def _robots_txt(*, site_url: str | None, base_path: str) -> str:
    _ = base_path
    origin = (site_url or "http://127.0.0.1:8080").rstrip("/")
    sitemap = f"{origin}/sitemap.xml"
    return "\n".join(
        (
            "User-agent: *",
            "Allow: /",
            "",
            f"Sitemap: {sitemap}",
            "",
        )
    )


def _write_hosting_files(output_dir: Path, *, site_url: str | None, base_path: str) -> None:
    (output_dir / ".nojekyll").touch()
    (output_dir / "robots.txt").write_text(
        _robots_txt(site_url=site_url, base_path=base_path),
        encoding="utf-8",
    )


def _index_txt_routes(docs_app: DocsApp) -> list[str]:
    from furatena.catalog.access import AccessPermission, accessible_nodes

    routes: list[str] = []
    nodes = docs_app.catalog.nodes
    public_nodes = accessible_nodes(
        docs_app.catalog,
        nodes,
        permission=AccessPermission.EXPORT,
    )
    public_urls = {node.url for node in public_nodes}
    for node in public_nodes:
        url = node.url.rstrip("/")
        routes.append(docs_app.catalog.scoped_url(f"{url}/index.txt"))
    i18n = docs_app.config.i18n
    if i18n.enabled and i18n.fallback_to_default:
        from furatena.catalog.i18n import collect_i18n_export_routes

        for url in collect_i18n_export_routes(docs_app.catalog, i18n):
            if url not in public_urls:
                continue
            routes.append(docs_app.catalog.scoped_url(f"{url.rstrip('/')}/index.txt"))
    return routes


def _effective_frozen_dir(docs_app: DocsApp, frozen_dir: Path | None) -> Path | None:
    if frozen_dir is None:
        return None
    return docs_app.catalog.frozen_root or scoped_frozen_dir(
        frozen_dir,
        docs_app.config.identity.to_meta(),
    )


def _prune_stale_outputs(output_dir: Path, *, keep_paths: set[Path]) -> int:
    removed = 0
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(output_dir)
        if rel.name in {".nojekyll", "robots.txt", "export.manifest.json"}:
            continue
        if rel not in keep_paths:
            path.unlink()
            removed += 1
    return removed


async def _export_async(docs_app: DocsApp, options: StaticExportOptions) -> StaticExportResult:
    from chirp.testing.client import TestClient

    from furatena.catalog.lifecycle import check_lifecycle_sources

    output_dir = options.output_dir.resolve()
    base_path = normalize_base_path(options.base_path or docs_base_path())
    frozen_dir = options.frozen_dir
    if frozen_dir is None and docs_app.serve.frozen_dir is not None:
        frozen_dir = docs_app.serve.frozen_dir
    frozen_dir = _effective_frozen_dir(docs_app, frozen_dir)
    if not options.allow_lifecycle_errors:
        lifecycle_errors, lifecycle_warnings = check_lifecycle_sources(docs_app.catalog)
        if lifecycle_errors:
            raise StaticExportLifecycleError(lifecycle_errors, lifecycle_warnings)

    if options.incremental and output_dir.is_dir():
        pass
    else:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

    prior_env = _configure_export_env(site_url=options.site_url, base_path=base_path)
    page_count = 0
    skipped_count = 0
    sidecar_count = 0
    written_paths: set[Path] = set()
    manifest_fps = _load_manifest_fingerprints(output_dir) if options.incremental else {}
    from furatena.catalog.renderer_fingerprint import (
        read_renderer_fingerprint,
        renderer_fingerprint,
    )

    docs_root = docs_app.config.root
    renderer_fp = read_renderer_fingerprint(frozen_dir) if frozen_dir else None
    if renderer_fp is None:
        renderer_fp = renderer_fingerprint(docs_root)
    route_fps: dict[str, str] = {}
    try:
        client = TestClient(docs_app.create_app())
        async with client:
            for url_path in _collect_routes(docs_app, options):
                fp = _route_fingerprint(docs_app, url_path, renderer_fp=renderer_fp, frozen_dir=frozen_dir)
                route_fps[url_path] = fp
                rel = url_path_to_output_file(url_path)
                if (
                    options.incremental
                    and manifest_fps.get(url_path) == fp
                    and (output_dir / rel).is_file()
                ):
                    skipped_count += 1
                    written_paths.add(rel)
                    continue
                response = await client.get(url_path)
                if response.status != 200:
                    raise RuntimeError(f"Export failed for {url_path}: HTTP {response.status}")
                content_type = _response_header(response.headers, "content-type", "text/html")
                if _maybe_skip_existing(
                    output_dir,
                    url_path,
                    body=response.text,
                    content_type=content_type,
                    base_path=base_path,
                    incremental=options.incremental,
                ):
                    skipped_count += 1
                    written_paths.add(url_path_to_output_file(url_path))
                    continue
                target = _write_response(
                    output_dir,
                    url_path,
                    body=response.text,
                    content_type=content_type,
                    base_path=base_path,
                )
                written_paths.add(target.relative_to(output_dir))
                page_count += 1

            for url_path in _sidecar_routes():
                name = url_path.lstrip("/")
                fp = _sidecar_fingerprint(name, renderer_fp=renderer_fp, frozen_dir=frozen_dir)
                route_fps[url_path] = fp
                rel = url_path_to_output_file(url_path)
                if (
                    options.incremental
                    and manifest_fps.get(url_path) == fp
                    and (output_dir / rel).is_file()
                ):
                    skipped_count += 1
                    written_paths.add(rel)
                    continue
                response = await client.get(url_path)
                if response.status != 200:
                    raise RuntimeError(f"Sidecar export failed for {url_path}: HTTP {response.status}")
                content_type = _response_header(
                    response.headers,
                    "content-type",
                    _default_content_type(url_path),
                )
                if _maybe_skip_existing(
                    output_dir,
                    url_path,
                    body=response.text,
                    content_type=content_type,
                    base_path=base_path,
                    incremental=options.incremental,
                ):
                    skipped_count += 1
                    written_paths.add(url_path_to_output_file(url_path))
                    continue
                target = _write_response(
                    output_dir,
                    url_path,
                    body=response.text,
                    content_type=content_type,
                    base_path=base_path,
                )
                written_paths.add(target.relative_to(output_dir))
                sidecar_count += 1

            if options.include_index_txt:
                for url_path in _index_txt_routes(docs_app):
                    doc_path = url_path.rsplit("/index.txt", 1)[0]
                    if not doc_path.endswith("/"):
                        doc_path = f"{doc_path}/"
                    node = docs_app.catalog.get_path(doc_path)
                    fp = (
                        _node_source_fingerprint(node, renderer_fp=renderer_fp, frozen_dir=frozen_dir)
                        + "|index.txt"
                        if node is not None
                        else hashlib.sha256(f"index.txt|{url_path}".encode()).hexdigest()[:16]
                    )
                    route_fps[url_path] = fp
                    rel = url_path_to_output_file(url_path)
                    if (
                        options.incremental
                        and manifest_fps.get(url_path) == fp
                        and (output_dir / rel).is_file()
                    ):
                        skipped_count += 1
                        written_paths.add(rel)
                        continue
                    response = await client.get(url_path)
                    if response.status != 200:
                        continue
                    if _maybe_skip_existing(
                        output_dir,
                        url_path,
                        body=response.text,
                        content_type="text/plain",
                        base_path=base_path,
                        incremental=options.incremental,
                    ):
                        skipped_count += 1
                        written_paths.add(url_path_to_output_file(url_path))
                        continue
                    target = _write_response(
                        output_dir,
                        url_path,
                        body=response.text,
                        content_type="text/plain",
                        base_path=base_path,
                    )
                    written_paths.add(target.relative_to(output_dir))
                    sidecar_count += 1

        for frozen_sidecar in ("semantic.json", "structure.json"):
            if _copy_frozen_sidecar(frozen_dir, frozen_sidecar, output_dir, base_path):
                written_paths.add(Path(frozen_sidecar))
                sidecar_count += 1

        if frozen_dir is not None:
            frozen_inventories = frozen_dir / "inventories"
            inventory_specs = tuple(
                getattr(getattr(docs_app.catalog, "inventory_store", None), "specs", ())
            )
            for spec in inventory_specs:
                source = frozen_inventories / f"{spec.id}.inv"
                if not source.is_file():
                    continue
                rel = Path("inventories") / spec.id / "objects.inv"
                target = output_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                written_paths.add(rel)
                sidecar_count += 1
            if inventory_specs:
                default_source = frozen_inventories / f"{inventory_specs[0].id}.inv"
                if default_source.is_file():
                    shutil.copy2(default_source, output_dir / "objects.inv")
                    written_paths.add(Path("objects.inv"))
                    sidecar_count += 1

        asset_mounts = _copy_theme_assets(docs_app, output_dir)
        _write_hosting_files(output_dir, site_url=options.site_url, base_path=base_path)
        written_paths.update({Path(".nojekyll"), Path("robots.txt")})

        if options.incremental:
            _prune_stale_outputs(output_dir, keep_paths=written_paths)

        written_paths.add(Path("channels.json"))
        manifest = {
            "schema_version": 2,
            "page_count": page_count,
            "skipped_count": skipped_count,
            "base_path": base_path or "/",
            "site_url": options.site_url or "",
            "incremental": options.incremental,
            "sidecars": [*list(_sidecar_routes()), "semantic.json", "structure.json"],
            "paths": sorted(str(path) for path in written_paths),
            "fingerprints": route_fps,
        }
        (output_dir / "export.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        channel_payload = channel_manifest(
            docs_app.catalog,
            config=docs_app.config,
            base_url=options.site_url or "",
            base_path=base_path,
            mode="static",
            paths=sorted(str(path) for path in written_paths),
            fingerprints=route_fps,
        )
        (output_dir / "channels.json").write_text(
            json.dumps(channel_payload, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        _restore_export_env(prior_env)

    return StaticExportResult(
        output_dir=output_dir,
        page_count=page_count,
        sidecar_count=sidecar_count,
        asset_mounts=asset_mounts,
        skipped_count=skipped_count,
    )


def export_static_site(docs_app: DocsApp, options: StaticExportOptions) -> StaticExportResult:
    """Render the docs app to a static directory tree."""
    return asyncio.run(_export_async(docs_app, options))
