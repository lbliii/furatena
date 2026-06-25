"""Export the federated doc catalog to disk."""

from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.assets import bundle_css, copy_fonts, copy_tree_files, write_assets_manifest
from furatena.catalog.vendor_paths import VENDOR_FILES, vendor_dir
from furatena.catalog.autodoc_cache import autodoc_fingerprint, write_autodoc_fingerprint
from furatena.catalog.config import load_docs_config
from furatena.catalog.embeddings import EmbeddingIndex
from furatena.catalog.export import catalog_graph, search_json, tools_manifest
from furatena.catalog.structure_index import build_structure_index
from furatena.catalog.inventories.sphinx import write_objects_inv_bytes
from furatena.catalog.freeze_incremental import (
    dirty_mount_ids,
    mount_content_fingerprint,
    write_freeze_manifest,
    write_mount_fingerprint,
)
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.renderer_fingerprint import (
    read_renderer_fingerprint,
    renderer_fingerprint,
    write_renderer_fingerprint,
)
from furatena.catalog.theme_pack import load_theme_pack
from furatena.catalog.seo import docs_base_url
from furatena.catalog.workers import resolve_workers

MOUNTS_CONFIG = ROOT / "mounts.yaml"
AUTODOC_CONFIG = REPO / "config" / "autodoc.yaml"


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
    for node in shard.nodes:
        slug_path = node.slug or "index"
        html = registry.body_html(node) if hasattr(registry, "body_html") else node.body_html
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

    return len(shard.nodes)


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


def _freeze_assets(out_dir: Path) -> None:
    docs = load_docs_config(ROOT / "docs.yaml")
    packaged = None
    try:
        from furatena.catalog.theme import _packaged_theme_assets

        packaged = _packaged_theme_assets(docs.theme.id, ROOT)
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
    write_assets_manifest(
        out_dir,
        theme_href=f"theme.{digest}.css",
        fonts_prefix=fonts_prefix,
        branding_prefix=branding_prefix,
        vendor_prefix=vendor_prefix,
    )


def main() -> None:
    argv = sys.argv[1:]
    full_rebuild = False
    workers: int | None = None
    while argv:
        if argv[0] == "--full":
            full_rebuild = True
            argv = argv[1:]
            continue
        if argv[0] == "--workers":
            if len(argv) < 2:
                raise SystemExit("--workers requires an integer")
            workers = int(argv[1])
            argv = argv[2:]
            continue
        break
    worker_count = resolve_workers(workers)
    out_dir = Path(argv[0]) if argv else ROOT / "frozen"
    out_dir.mkdir(parents=True, exist_ok=True)

    docs_config = load_docs_config(ROOT / "docs.yaml")
    index_start = time.perf_counter()
    registry = CatalogRegistry.from_config(
        MOUNTS_CONFIG,
        repo_root=REPO,
        app_root=ROOT,
        rewrites_path=docs_config.rewrites_path,
        inventories_path=docs_config.inventories_path,
        autodoc_config=AUTODOC_CONFIG,
        autodoc=True,
        workers=worker_count,
    )
    index_seconds = time.perf_counter() - index_start
    base = docs_base_url()

    registry_manifest: dict = {
        "schema_version": 2,
        "mounts": [
            {
                "id": mount.id,
                "label": mount.label,
                "url_prefix": mount.url_prefix,
                "default": mount.default,
            }
            for mount in registry.mounts
        ],
    }
    inventories = registry.inventories_metadata()
    if inventories:
        registry_manifest["inventories"] = inventories
    (out_dir / "registry.json").write_text(
        json.dumps(registry_manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    renderer_fp = renderer_fingerprint(
        ROOT,
        theme_id=docs_config.theme.id,
        skin_pack_root=(
            load_theme_pack(docs_config.theme.use).root if docs_config.theme.use else None
        ),
    )
    stored_renderer = read_renderer_fingerprint(out_dir)
    renderer_changed = full_rebuild or stored_renderer != renderer_fp
    mounts_to_freeze = [mount.id for mount in registry.mounts] if full_rebuild else dirty_mount_ids(
        registry,
        out_dir,
        renderer_changed=renderer_changed,
    )

    total = 0
    export_start = time.perf_counter()
    if mounts_to_freeze:
        for mount in registry.mounts:
            if mount.id not in mounts_to_freeze:
                total += len(registry._shards[mount.id].nodes)
                continue
            total += _freeze_shard(registry, mount.id, out_dir, workers=worker_count)
            mount_dir = out_dir / "mounts" / mount.id
            shard = registry._shards[mount.id]
            write_mount_fingerprint(
                mount_dir,
                mount_content_fingerprint(shard, mount.content_root),
            )
    else:
        total = len(registry.nodes)
        print(f"All mounts current — skipped shard freeze ({total} pages)")

    if mounts_to_freeze:
        merged_graph = catalog_graph(registry)
        from furatena.catalog.dcp_validate import validate_catalog_payload

        dcp_errors = validate_catalog_payload(merged_graph)
        if dcp_errors:
            preview = "\n".join(f"  - {line}" for line in dcp_errors[:8])
            extra = ""
            if len(dcp_errors) > 8:
                extra = f"\n  ... and {len(dcp_errors) - 8} more"
            raise SystemExit(f"catalog.json failed DCP v3 validation:\n{preview}{extra}")
        (out_dir / "catalog.json").write_text(json.dumps(merged_graph, indent=2) + "\n", encoding="utf-8")
        (out_dir / "search.json").write_text(
            json.dumps(search_json(registry, base_url=base), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "tools.json").write_text(
            json.dumps(tools_manifest(registry, base_url=base), indent=2) + "\n",
            encoding="utf-8",
        )
        (out_dir / "structure.json").write_text(
            json.dumps(build_structure_index(registry), indent=2) + "\n",
            encoding="utf-8",
        )
        _freeze_inventories(registry, out_dir)
        semantic = EmbeddingIndex.from_nodes(
            list(registry.nodes),
            documents=registry.ast_documents(),
        )
        semantic.write(out_dir / "semantic.json")

    write_autodoc_fingerprint(out_dir, autodoc_fingerprint(AUTODOC_CONFIG, repo_root=REPO))
    write_renderer_fingerprint(out_dir, renderer_fp)
    if mounts_to_freeze or not (out_dir / "assets").is_dir():
        _freeze_assets(out_dir)
    write_freeze_manifest(out_dir, dirty_mounts=mounts_to_freeze, total_pages=total)
    export_seconds = time.perf_counter() - export_start

    if mounts_to_freeze:
        print(
            f"Froze {len(mounts_to_freeze)} mount(s), {total} pages total → {out_dir} "
            f"(index {index_seconds:.1f}s, export {export_seconds:.1f}s, {worker_count} workers)"
        )
    else:
        print(
            f"Freeze up to date — {total} pages at {out_dir} "
            f"(index {index_seconds:.1f}s, {worker_count} workers)"
        )


if __name__ == "__main__":
    main()
