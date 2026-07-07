"""Freeze a Furatena catalog into portable on-disk IR."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.assets import (
    bundle_css,
    copy_fonts,
    copy_tree_files,
    write_assets_manifest,
)
from furatena.catalog.autodoc_cache import autodoc_fingerprint, write_autodoc_fingerprint
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.config import load_docs_config
from furatena.catalog.deployment_profiles import deployment_profiles_manifest
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.exceptions import ExportError
from furatena.catalog.export import (
    api_operations_json,
    catalog_graph,
    llms_full_txt,
    llms_txt,
    meta_json,
    search_json,
    surface_json,
    tools_manifest,
)
from furatena.catalog.freeze_incremental import (
    mount_content_fingerprint,
    mount_source_statuses,
    set_mount_freeze_status,
    write_freeze_manifest,
    write_mount_fingerprint,
)
from furatena.catalog.identity import scoped_frozen_dir
from furatena.catalog.inventories.sphinx import write_objects_inv_bytes
from furatena.catalog.packaging import prune_stale_files, validate_packaging_lifecycle
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.renderer_fingerprint import (
    read_renderer_fingerprint,
    renderer_fingerprint,
    write_renderer_fingerprint,
)
from furatena.catalog.seo import docs_base_url
from furatena.catalog.structure_index import build_structure_index
from furatena.catalog.theme_pack import load_theme_pack
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir
from furatena.catalog.workers import resolve_workers


@dataclass(frozen=True, slots=True)
class FreezeCatalogOptions:
    """Inputs for a catalog freeze."""

    docs_config: Path
    app_root: Path
    repo_root: Path
    output_dir: Path
    full_rebuild: bool = False
    workers: int | None = None
    autodoc: bool = True
    autodoc_config: Path | None = None
    allow_lifecycle_errors: bool = False


@dataclass(frozen=True, slots=True)
class FreezeCatalogResult:
    """Summary of a completed catalog freeze."""

    output_dir: Path
    frozen_mounts: tuple[str, ...]
    page_count: int
    worker_count: int
    index_seconds: float
    export_seconds: float


def _write_frozen_page(
    *,
    pages_dir: Path,
    mount_dir: Path,
    slug_path: str,
    html: str,
    ast_json: str | None,
) -> None:
    target = pages_dir / f"{slug_path}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html + "\n", encoding="utf-8")
    if ast_json:
        ast_dir = mount_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        ast_target = ast_dir / f"{slug_path}.json"
        ast_target.parent.mkdir(parents=True, exist_ok=True)
        ast_target.write_text(ast_json + "\n", encoding="utf-8")


def _freeze_shard(registry: CatalogRegistry, mount_id: str, out_dir: Path, *, workers: int) -> int:
    shard = registry._shards[mount_id]
    mount_dir = out_dir / "mounts" / mount_id
    pages_dir = mount_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    graph = catalog_graph(shard)
    graph["mount"] = mount_id
    (mount_dir / "catalog.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    jobs: list[tuple[Path, Path, str, str, str | None]] = []
    keep_pages: set[Path] = set()
    keep_ast: set[Path] = set()
    for node in accessible_nodes(
        registry,
        shard.nodes,
        permission=AccessPermission.EXPORT,
    ):
        slug_path = node.slug or "index"
        html = registry.body_html(node) if hasattr(registry, "body_html") else node.body_html
        keep_pages.add(pages_dir / f"{slug_path}.html")
        if node.ast_json:
            keep_ast.add(mount_dir / "ast" / f"{slug_path}.json")
        jobs.append((pages_dir, mount_dir, slug_path, html, node.ast_json))

    if workers > 1 and len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(
                pool.map(
                    lambda args: _write_frozen_page(
                        pages_dir=args[0],
                        mount_dir=args[1],
                        slug_path=args[2],
                        html=args[3],
                        ast_json=args[4],
                    ),
                    jobs,
                )
            )
    else:
        for pages_path, mount_path, slug_path, html, ast_json in jobs:
            _write_frozen_page(
                pages_dir=pages_path,
                mount_dir=mount_path,
                slug_path=slug_path,
                html=html,
                ast_json=ast_json,
            )

    prune_stale_files(pages_dir, keep_pages, suffixes=(".html",))
    prune_stale_files(mount_dir / "ast", keep_ast, suffixes=(".json",))

    return len(jobs)


def _freeze_inventories(registry: CatalogRegistry, out_dir: Path) -> None:
    store = registry.inventory_store
    if store is None or not store.entries:
        return
    inv_dir = out_dir / "inventories"
    inv_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list] = {}
    for entry in store.entries.values():
        grouped.setdefault(entry.inventory_id, []).append(entry)
    for inventory_id, entries in grouped.items():
        if not inventory_id:
            continue
        payload = write_objects_inv_bytes(
            tuple(entries),
            project=inventory_id,
            version=registry.active_channel,
        )
        (inv_dir / f"{inventory_id}.inv").write_bytes(payload)


def _freeze_assets(out_dir: Path, *, app_root: Path, theme_id: str, skin_pack: str | None) -> None:
    packaged = None
    try:
        from furatena.catalog.theme import _packaged_theme_assets

        packaged = _packaged_theme_assets(theme_id, app_root)
    except Exception:
        packaged = None
    if packaged is None:
        return
    css_dir, fonts_dir, branding_dir = packaged
    assets_dir = out_dir / "assets"
    bundle_path, digest = bundle_css(css_dir / "style.css", cache_dir=assets_dir)
    final = assets_dir / f"theme.{digest}.css"
    if bundle_path != final:
        final.write_text(bundle_path.read_text(encoding="utf-8"), encoding="utf-8")
        bundle_path.unlink(missing_ok=True)
    fonts_prefix = None
    if fonts_dir is not None:
        copy_fonts(fonts_dir, assets_dir / "fonts")
        fonts_prefix = "fonts"
    branding_prefix = None
    if branding_dir is not None:
        copy_tree_files(
            branding_dir,
            assets_dir / "branding",
            names=(
                "favicon.svg",
                "favicon.ico",
                "favicon-16x16.png",
                "favicon-32x32.png",
                "apple-touch-icon.png",
                "site.webmanifest",
            ),
        )
        branding_prefix = "branding"
    vendor_prefix = None
    vendor_src = Path(vendor_dir())
    if vendor_src.is_dir() and all((vendor_src / name).is_file() for name in VENDOR_FILES):
        copy_tree_files(vendor_src, assets_dir / "vendor", names=VENDOR_FILES)
        vendor_prefix = "vendor"
    _ = skin_pack
    write_assets_manifest(
        out_dir,
        theme_href=f"theme.{digest}.css",
        fonts_prefix=fonts_prefix,
        branding_prefix=branding_prefix,
        vendor_prefix=vendor_prefix,
    )


def _registry_manifest(
    registry: CatalogRegistry, mount_status: dict[str, dict] | None = None
) -> dict:
    payload: dict = {
        "schema_version": 2,
        "mounts": [
            {
                "id": mount.id,
                "label": mount.label,
                "url_prefix": mount.url_prefix,
                "default": mount.default,
                **(
                    {"source_status": _public_mount_status(mount_status[mount.id])}
                    if mount_status is not None and mount.id in mount_status
                    else {}
                ),
            }
            for mount in registry.mounts
        ],
    }
    inventories = registry.inventories_metadata()
    if inventories:
        payload["inventories"] = inventories
    return payload


def _public_mount_status(status: dict) -> dict:
    return {key: value for key, value in status.items() if key not in {"dirty", "source_root"}}


def _write_registry_manifest(
    out_dir: Path,
    registry: CatalogRegistry,
    *,
    mount_status: dict[str, dict] | None = None,
) -> None:
    (out_dir / "registry.json").write_text(
        json.dumps(_registry_manifest(registry, mount_status), indent=2) + "\n",
        encoding="utf-8",
    )


def freeze_catalog(options: FreezeCatalogOptions) -> FreezeCatalogResult:
    """Write frozen catalog files for a docs app."""
    base_out_dir = options.output_dir.resolve()
    base_out_dir.mkdir(parents=True, exist_ok=True)
    worker_count = resolve_workers(options.workers)

    docs_config = load_docs_config(options.docs_config)
    out_dir = scoped_frozen_dir(base_out_dir, docs_config.identity.to_meta())
    out_dir.mkdir(parents=True, exist_ok=True)
    mounts_path = docs_config.mounts_path or options.app_root / "mounts.yaml"
    index_start = time.perf_counter()
    registry = CatalogRegistry.from_config(
        mounts_path,
        repo_root=options.repo_root,
        app_root=options.app_root,
        rewrites_path=docs_config.rewrites_path,
        inventories_path=docs_config.inventories_path,
        catalog_identity=docs_config.identity.to_meta(),
        autodoc_config=options.autodoc_config,
        autodoc=options.autodoc,
        workers=worker_count,
    )
    validate_packaging_lifecycle(
        registry,
        target="catalog freeze",
        allow_errors=options.allow_lifecycle_errors,
    )
    index_seconds = time.perf_counter() - index_start
    base = docs_base_url()

    skin_pack_root = load_theme_pack(docs_config.theme.use).root if docs_config.theme.use else None
    renderer_fp = renderer_fingerprint(
        options.app_root,
        theme_id=docs_config.theme.id,
        skin_pack_root=skin_pack_root,
    )
    stored_renderer = read_renderer_fingerprint(out_dir)
    renderer_changed = options.full_rebuild or stored_renderer != renderer_fp
    mount_status = mount_source_statuses(
        registry,
        out_dir,
        renderer_fingerprint=renderer_fp,
        renderer_changed=renderer_changed,
        full_rebuild=options.full_rebuild,
    )
    mounts_to_freeze = [mount.id for mount in registry.mounts if mount_status[mount.id]["dirty"]]

    total = 0
    failed_mounts: dict[str, str] = {}
    export_start = time.perf_counter()
    if mounts_to_freeze:
        for mount in registry.mounts:
            status = mount_status[mount.id]
            if mount.id not in mounts_to_freeze:
                set_mount_freeze_status(status, "skipped")
                total += len(registry._shards[mount.id].nodes)
                continue
            try:
                total += _freeze_shard(registry, mount.id, out_dir, workers=worker_count)
                mount_dir = out_dir / "mounts" / mount.id
                shard = registry._shards[mount.id]
                fingerprint = mount_content_fingerprint(shard, mount.content_root)
                write_mount_fingerprint(mount_dir, fingerprint)
                status["content_fingerprint"] = fingerprint
                set_mount_freeze_status(status, "frozen")
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                failed_mounts[mount.id] = message
                set_mount_freeze_status(status, "failed", error=message)
    else:
        for status in mount_status.values():
            set_mount_freeze_status(status, "skipped")
        total = len(registry.nodes)

    _write_registry_manifest(out_dir, registry, mount_status=mount_status)

    required_agent_sidecars = (
        "catalog.json",
        "search.json",
        "tools.json",
        "catalog/api-operations.json",
        "llms.txt",
        "llms-full.txt",
        "meta.json",
        "semantic.json",
        "structure.json",
        "surface.json",
        "deployment-profiles.json",
        "channels.json",
    )
    if not failed_mounts and (
        mounts_to_freeze or any(not (out_dir / path).is_file() for path in required_agent_sidecars)
    ):
        merged_graph = catalog_graph(registry)
        from furatena.catalog.dcp_validate import validate_catalog_payload

        dcp_errors = validate_catalog_payload(merged_graph)
        if dcp_errors:
            preview = "\n".join(f"  - {line}" for line in dcp_errors[:8])
            extra = f"\n  ... and {len(dcp_errors) - 8} more" if len(dcp_errors) > 8 else ""
            raise ExportError(
                f"catalog.json failed DCP v3 validation:\n{preview}{extra}",
                path=out_dir / "catalog.json",
                operation="freeze_catalog",
            )
        (out_dir / "catalog.json").write_text(
            json.dumps(merged_graph, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / "search.json").write_text(
            json.dumps(search_json(registry, base_url=base), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "tools.json").write_text(
            json.dumps(
                tools_manifest(
                    registry,
                    base_url=base,
                    site_name=docs_config.site.name,
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        api_operations_path = out_dir / "catalog" / "api-operations.json"
        api_operations_path.parent.mkdir(parents=True, exist_ok=True)
        api_operations_path.write_text(
            json.dumps(api_operations_json(registry, base_url=base), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "structure.json").write_text(
            json.dumps(build_structure_index(registry), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "llms.txt").write_text(
            llms_txt(registry, site_name=docs_config.site.name),
            encoding="utf-8",
        )
        (out_dir / "llms-full.txt").write_text(
            llms_full_txt(registry, site_name=docs_config.site.name),
            encoding="utf-8",
        )
        (out_dir / "meta.json").write_text(
            json.dumps(meta_json(registry), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "surface.json").write_text(
            json.dumps(surface_json(docs_config, registry), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "deployment-profiles.json").write_text(
            json.dumps(deployment_profiles_manifest(base_url=base), indent=2) + "\n",
            encoding="utf-8",
        )
        semantic = EmbeddingIndex.from_nodes(
            accessible_nodes(
                registry,
                registry.nodes,
                permission=AccessPermission.EXPORT,
            ),
            documents=registry.ast_documents(),
        )
        semantic.write(out_dir / "semantic.json")
        _freeze_inventories(registry, out_dir)
        artifact_paths = [*required_agent_sidecars[:-1]]
        inventory_store = registry.inventory_store
        if inventory_store is not None and inventory_store.entries:
            from furatena.catalog.inventories.export import inventories_json, inventory_bytes

            (out_dir / "inventories.json").write_text(
                json.dumps(
                    inventories_json(registry, base_url=base, frozen_dir=out_dir),
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            artifact_paths.append("inventories.json")
            if inventory_store.specs:
                default_inventory = inventory_bytes(
                    registry,
                    inventory_store.specs[0].id,
                    frozen_dir=out_dir,
                )
                if default_inventory is not None:
                    (out_dir / "objects.inv").write_bytes(default_inventory)
                    artifact_paths.append("objects.inv")
        channel_payload = channel_manifest(
            registry,
            config=docs_config,
            base_url=base,
            mode="freeze",
            paths=artifact_paths,
            mount_status=mount_status,
            renderer_fingerprint=renderer_fp,
        )
        (out_dir / "channels.json").write_text(
            json.dumps(channel_payload, indent=2) + "\n",
            encoding="utf-8",
        )

    if failed_mounts:
        write_freeze_manifest(
            out_dir,
            dirty_mounts=mounts_to_freeze,
            total_pages=total,
            mount_statuses=mount_status,
            renderer_fingerprint=renderer_fp,
            renderer_changed=renderer_changed,
        )
        formatted = ", ".join(f"{mount}: {error}" for mount, error in sorted(failed_mounts.items()))
        raise ExportError(
            f"freeze failed for mount(s): {formatted}",
            path=out_dir,
            mount=",".join(sorted(failed_mounts)),
            operation="freeze_mounts",
        )

    if options.autodoc_config is not None:
        write_autodoc_fingerprint(
            out_dir,
            autodoc_fingerprint(options.autodoc_config, repo_root=options.repo_root),
        )
    write_renderer_fingerprint(out_dir, renderer_fp)
    if mounts_to_freeze or not (out_dir / "assets").is_dir():
        _freeze_assets(
            out_dir,
            app_root=options.app_root,
            theme_id=docs_config.theme.id,
            skin_pack=docs_config.theme.use,
        )
    write_freeze_manifest(
        out_dir,
        dirty_mounts=mounts_to_freeze,
        total_pages=total,
        mount_statuses=mount_status,
        renderer_fingerprint=renderer_fp,
        renderer_changed=renderer_changed,
    )
    export_seconds = time.perf_counter() - export_start

    return FreezeCatalogResult(
        output_dir=out_dir,
        frozen_mounts=tuple(mounts_to_freeze),
        page_count=total,
        worker_count=worker_count,
        index_seconds=index_seconds,
        export_seconds=export_seconds,
    )
