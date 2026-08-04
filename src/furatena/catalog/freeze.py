"""Freeze a Furatena catalog into portable on-disk IR."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from furatena.catalog.access import AccessPermission, accessible_nodes
from furatena.catalog.application_roots import ApplicationRoots
from furatena.catalog.assets import (
    bundle_css,
    copy_fonts,
    copy_tree_files,
    write_assets_manifest,
)
from furatena.catalog.atomic_directory import AtomicDirectoryTransaction
from furatena.catalog.autodoc_cache import autodoc_fingerprint, write_autodoc_fingerprint
from furatena.catalog.catalog_shards import catalog_shard_path
from furatena.catalog.channel_manifest import channel_manifest
from furatena.catalog.config import load_docs_config
from furatena.catalog.deployment_manifest import DeploymentManifest, write_deployment_manifest
from furatena.catalog.deployment_profiles import deployment_profiles_manifest
from furatena.catalog.edition_projection import (
    EDITION_PROJECTION_FILENAME,
    write_edition_projection,
)
from furatena.catalog.edition_shards import EditionShardStatus, freeze_edition_shards
from furatena.catalog.embedding_providers import build_embedding_index
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
from furatena.catalog.operation_lease import (
    OperationLease,
    operation_lease_seconds,
    operation_timeout_seconds,
)
from furatena.catalog.packaging import prune_stale_files, validate_packaging_lifecycle
from furatena.catalog.presentation_pack import resolve_presentation
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.renderer_fingerprint import (
    read_renderer_fingerprint,
    renderer_fingerprint,
    write_renderer_fingerprint,
)
from furatena.catalog.seo import docs_base_url
from furatena.catalog.shard_discovery import (
    discovery_mount_token,
    discovery_mounts,
    llms_hub_txt,
    sitemap_index_xml,
)
from furatena.catalog.sitemap import sitemap_xml
from furatena.catalog.structure_index import build_structure_index
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir
from furatena.catalog.version_artifacts import (
    public_versioned_mounts,
    versions_for_mount,
    versions_manifest,
    versions_mount_path,
)
from furatena.catalog.workers import resolve_workers


@dataclass(frozen=True, slots=True)
class FreezeCatalogOptions:
    """Inputs for a catalog freeze."""

    docs_config: Path
    app_root: Path
    repo_root: Path
    output_dir: Path
    platform_root: Path | None = None
    state_root: Path | None = None
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
    frozen_editions: tuple[str, ...] = ()
    reused_editions: tuple[str, ...] = ()
    edition_seconds: float = 0.0


def _compact_json(payload: object) -> str:
    return json.dumps(payload, separators=(",", ":")) + "\n"


def _write_version_artifacts(
    out_dir: Path,
    registry: CatalogRegistry,
    *,
    base_url: str,
) -> tuple[list[str], bool]:
    """Refresh version artifacts and report whether their public bytes changed."""
    payloads = {"versions.json": _compact_json(versions_manifest(registry, base_url=base_url))}
    mount_paths: list[str] = []
    for mount in public_versioned_mounts(registry):
        relative_path = versions_mount_path(str(mount.id))
        if relative_path is None:
            continue
        relative = relative_path.as_posix()
        payloads[relative] = _compact_json(
            versions_for_mount(registry, mount.id, base_url=base_url)
        )
        mount_paths.append(relative)

    versions_root = out_dir / "versions" / "mounts"
    existing = {
        path.relative_to(out_dir).as_posix()
        for path in versions_root.glob("*.json")
        if path.is_file()
    }
    stale = existing - payloads.keys()
    changed = bool(stale)
    for relative, body in payloads.items():
        target = out_dir / relative
        previous = target.read_text(encoding="utf-8") if target.is_file() else None
        changed = changed or previous != body
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    for relative in stale:
        (out_dir / relative).unlink()
    return mount_paths, changed


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
    registry: CatalogRegistry,
    mount_status: dict[str, dict] | None = None,
    edition_status: tuple[EditionShardStatus, ...] = (),
) -> dict:
    editions_by_mount: dict[str, list[dict]] = {}
    for status in edition_status:
        editions_by_mount.setdefault(status.mount, []).append(status.public_record())
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
                **(
                    {"editions": editions_by_mount[mount.id]}
                    if mount.id in editions_by_mount
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
    edition_status: tuple[EditionShardStatus, ...] = (),
) -> None:
    (out_dir / "registry.json").write_text(
        json.dumps(
            _registry_manifest(registry, mount_status, edition_status),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_catalog_shard_route_alias(out_dir: Path, mount_id: str) -> None:
    """Expose the frozen mount shard at its public catalog route path."""
    route_path = catalog_shard_path(mount_id)
    if route_path is None:
        return
    source = out_dir / "mounts" / mount_id / "catalog.json"
    if not source.is_file():
        return
    target = out_dir / route_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def freeze_catalog(options: FreezeCatalogOptions) -> FreezeCatalogResult:
    """Write frozen catalog files for a docs app."""
    state_root = options.state_root or options.app_root / ".docs-cache"
    with OperationLease(
        state_root / "operation-leases",
        "deployment",
        resource=str(options.output_dir.resolve()),
        timeout_seconds=operation_timeout_seconds(),
        lease_seconds=operation_lease_seconds(),
    ):
        target = options.output_dir.resolve()
        transaction = AtomicDirectoryTransaction(target, operation="freeze")
        staging = transaction.prepare()
        try:
            try:
                staged_result = _freeze_catalog_locked(replace(options, output_dir=staging))
            except ExportError:
                if not target.exists():
                    transaction.commit()
                raise
            relative_output = staged_result.output_dir.relative_to(staging)
            transaction.commit()
            return replace(staged_result, output_dir=target / relative_output)
        finally:
            transaction.cleanup()


def _freeze_catalog_locked(options: FreezeCatalogOptions) -> FreezeCatalogResult:
    base_out_dir = options.output_dir.resolve()
    base_out_dir.mkdir(parents=True, exist_ok=True)
    worker_count = resolve_workers(options.workers)

    docs_config = load_docs_config(options.docs_config)
    managed_roots = None
    if options.platform_root is not None and options.state_root is not None:
        managed_roots = ApplicationRoots(
            site=options.app_root.resolve(),
            platform=options.platform_root.resolve(),
            state=options.state_root.resolve(),
            output=options.output_dir.resolve(),
            managed=True,
        )
        managed_roots.validate()
        for label, path in (
            ("docs configuration", options.docs_config),
            ("mount configuration", docs_config.mounts_path),
            ("rewrite configuration", docs_config.rewrites_path),
            ("inventory configuration", docs_config.inventories_path),
            ("locale directory", docs_config.locales_dir),
        ):
            if path is not None:
                managed_roots.require_site_path(path, label=label)
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
        state_root=(options.state_root / "source-sync-state" if options.state_root else None),
    )
    if managed_roots is not None:
        for mount in registry.mounts:
            managed_roots.require_site_path(
                mount.content_root,
                label=f"content root for mount {mount.id!r}",
            )
    presentation_roots = managed_roots or ApplicationRoots(
        site=options.app_root.resolve(),
        platform=(options.platform_root or options.app_root).resolve(),
        state=(options.state_root or options.app_root / ".docs-cache").resolve(),
        output=base_out_dir.resolve(),
        managed=False,
    )
    presentation = resolve_presentation(docs_config, roots=presentation_roots)
    validate_packaging_lifecycle(
        registry,
        target="catalog freeze",
        allow_errors=options.allow_lifecycle_errors,
    )
    index_seconds = time.perf_counter() - index_start
    base = docs_base_url()

    skin_pack_root = presentation.skin.root if presentation.skin is not None else None
    renderer_fp = renderer_fingerprint(
        options.app_root,
        theme_id=docs_config.theme.id,
        skin_pack_root=skin_pack_root,
        platform_root=options.platform_root,
        presentation_digest=presentation.record.content_digest,
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

    edition_start = time.perf_counter()
    edition_status = () if failed_mounts else freeze_edition_shards(registry, out_dir)
    edition_seconds = time.perf_counter() - edition_start
    frozen_editions = tuple(
        f"{status.mount}:{status.edition}" for status in edition_status if status.status == "frozen"
    )
    reused_editions = tuple(
        f"{status.mount}:{status.edition}" for status in edition_status if status.status == "reused"
    )
    has_edition_projection = (
        bool(registry.edition_projection().pages) if not failed_mounts else False
    )
    edition_projection_changed = (
        write_edition_projection(registry, out_dir) if not failed_mounts else False
    )

    for mount in registry.mounts:
        _write_catalog_shard_route_alias(out_dir, mount.id)

    _write_registry_manifest(
        out_dir,
        registry,
        mount_status=mount_status,
        edition_status=edition_status,
    )

    version_artifact_paths, versions_changed = _write_version_artifacts(
        out_dir,
        registry,
        base_url=base,
    )

    required_agent_sidecars = (
        "catalog.json",
        "search.json",
        "tools.json",
        "catalog/api-operations.json",
        "sitemap.xml",
        "llms.txt",
        "llms-full.txt",
        "meta.json",
        "semantic.json",
        "structure.json",
        "surface.json",
        "deployment-profiles.json",
        "versions.json",
        *((EDITION_PROJECTION_FILENAME,) if has_edition_projection else ()),
        "channels.json",
    )
    visible_discovery_mounts = discovery_mounts(registry)
    visible_discovery_tokens = {
        discovery_mount_token(mount.id) for mount in visible_discovery_mounts
    }
    sitemap_hub = sitemap_index_xml(registry, base_url=base)
    llms_hub = llms_hub_txt(
        registry,
        site_name=docs_config.site.name,
        site_description=docs_config.site.description,
    )
    expected_sitemaps = {f"{token}.xml" for token in visible_discovery_tokens}
    expected_llms = {f"{token}.txt" for token in visible_discovery_tokens}
    current_sitemaps = {
        path.name for path in (out_dir / "sitemaps").glob("*.xml") if path.is_file()
    }
    current_llms = {path.name for path in (out_dir / "llms").glob("*.txt") if path.is_file()}
    discovery_changed = (
        not (out_dir / "sitemap.xml").is_file()
        or (out_dir / "sitemap.xml").read_text(encoding="utf-8") != sitemap_hub
        or not (out_dir / "llms.txt").is_file()
        or (out_dir / "llms.txt").read_text(encoding="utf-8") != llms_hub
        or current_sitemaps != expected_sitemaps
        or current_llms != expected_llms
    )
    if not failed_mounts and (
        mounts_to_freeze
        or frozen_editions
        or versions_changed
        or discovery_changed
        or edition_projection_changed
        or any(not (out_dir / path).is_file() for path in required_agent_sidecars)
        or any(
            not (out_dir / "sitemaps" / f"{discovery_mount_token(mount.id)}.xml").is_file()
            or not (out_dir / "llms" / f"{discovery_mount_token(mount.id)}.txt").is_file()
            for mount in visible_discovery_mounts
        )
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
        (out_dir / "catalog.json").write_text(_compact_json(merged_graph), encoding="utf-8")
        (out_dir / "search.json").write_text(
            _compact_json(search_json(registry, base_url=base)),
            encoding="utf-8",
        )
        (out_dir / "tools.json").write_text(
            _compact_json(
                tools_manifest(
                    registry,
                    base_url=base,
                    site_name=docs_config.site.name,
                )
            ),
            encoding="utf-8",
        )
        api_operations_path = out_dir / "catalog" / "api-operations.json"
        api_operations_path.parent.mkdir(parents=True, exist_ok=True)
        api_operations_path.write_text(
            _compact_json(api_operations_json(registry, base_url=base)),
            encoding="utf-8",
        )
        (out_dir / "structure.json").write_text(
            _compact_json(build_structure_index(registry)),
            encoding="utf-8",
        )
        (out_dir / "sitemap.xml").write_text(
            sitemap_hub,
            encoding="utf-8",
        )
        (out_dir / "llms.txt").write_text(
            llms_hub,
            encoding="utf-8",
        )
        mount_discovery_paths: list[str] = []
        for mount in visible_discovery_mounts:
            token = discovery_mount_token(mount.id)
            sitemap_path = Path("sitemaps") / f"{token}.xml"
            llms_path = Path("llms") / f"{token}.txt"
            (out_dir / sitemap_path).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / llms_path).parent.mkdir(parents=True, exist_ok=True)
            (out_dir / sitemap_path).write_text(
                sitemap_xml(registry, base_url=base, mount=mount.id),
                encoding="utf-8",
            )
            (out_dir / llms_path).write_text(
                llms_txt(
                    registry,
                    site_name=docs_config.site.name,
                    site_description=docs_config.site.description,
                    mount=mount.id,
                ),
                encoding="utf-8",
            )
            mount_discovery_paths.extend((sitemap_path.as_posix(), llms_path.as_posix()))
        prune_stale_files(
            out_dir / "sitemaps",
            {Path(name) for name in expected_sitemaps},
            suffixes=(".xml",),
        )
        prune_stale_files(
            out_dir / "llms",
            {Path(name) for name in expected_llms},
            suffixes=(".txt",),
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
        semantic = build_embedding_index(
            accessible_nodes(
                registry,
                registry.nodes,
                permission=AccessPermission.EXPORT,
            ),
            documents=registry.ast_documents(),
        )
        (out_dir / "semantic.json").write_text(
            _compact_json(semantic.to_json()),
            encoding="utf-8",
        )
        _freeze_inventories(registry, out_dir)
        artifact_paths = [*required_agent_sidecars[:-1], *mount_discovery_paths]
        artifact_paths.extend(version_artifact_paths)
        artifact_paths.extend(
            route_path.as_posix()
            for mount in registry.mounts
            if (route_path := catalog_shard_path(mount.id)) is not None
        )
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
        write_deployment_manifest(
            out_dir / "channels.json",
            DeploymentManifest.from_dict(channel_payload, target_hint="channels"),
        )

    if failed_mounts:
        write_freeze_manifest(
            out_dir,
            dirty_mounts=mounts_to_freeze,
            total_pages=total,
            mount_statuses=mount_status,
            renderer_fingerprint=renderer_fp,
            renderer_changed=renderer_changed,
            edition_statuses=[status.public_record() for status in edition_status],
            presentation=presentation.record.to_dict(),
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
        edition_statuses=[status.public_record() for status in edition_status],
        presentation=presentation.record.to_dict(),
    )
    export_seconds = time.perf_counter() - export_start

    return FreezeCatalogResult(
        output_dir=out_dir,
        frozen_mounts=tuple(mounts_to_freeze),
        page_count=total,
        worker_count=worker_count,
        index_seconds=index_seconds,
        export_seconds=export_seconds,
        frozen_editions=frozen_editions,
        reused_editions=reused_editions,
        edition_seconds=edition_seconds,
    )
