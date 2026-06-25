"""Spike: measure docs catalog index/freeze with threaded workers on 3.14t.

Run from repo root::

    uv run --extra markdown --group docs python app/spike_threading.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.config import load_docs_config
from furatena.catalog.export import catalog_graph
from furatena.catalog.registry import CatalogRegistry, load_mounts

MOUNTS_CONFIG = ROOT / "mounts.yaml"
AUTODOC_CONFIG = REPO / "site" / "config" / "_default" / "autodoc.yaml"


@dataclass(frozen=True, slots=True)
class Timing:
    label: str
    seconds: float
    detail: str = ""


def _gil_status() -> str:
    enabled = getattr(sys, "_is_gil_enabled", lambda: None)()
    if enabled is None:
        return "unknown"
    return "enabled" if enabled else "disabled (free-threading)"


def _load_registry() -> CatalogRegistry:
    docs_config = load_docs_config(ROOT / "docs.yaml")
    return CatalogRegistry.from_config(
        MOUNTS_CONFIG,
        repo_root=REPO,
        app_root=ROOT,
        rewrites_path=docs_config.rewrites_path,
        inventories_path=docs_config.inventories_path,
        autodoc_config=AUTODOC_CONFIG,
        autodoc=True,
    )


def _build_mount_shard(
    mount,
    *,
    repo_root: Path,
    app_root: Path,
    autodoc_config: Path,
    federated_slug_urls: dict[str, str],
    cached_autodoc,
) -> tuple[str, object, int]:
    from furatena.catalog.loader import DocCatalog

    shard = DocCatalog(
        mount.content_root,
        autodoc_config=autodoc_config if mount.default else None,
        repo_root=repo_root,
        autodoc=mount.default,
        mount=mount.id,
        url_prefix=mount.url_prefix,
        cached_autodoc_nodes=cached_autodoc if mount.default else None,
        source_config=mount.source,
        federated_slug_urls=federated_slug_urls,
    )
    return mount.id, shard, len(shard.nodes)


def _parallel_registry_load(workers: int) -> CatalogRegistry:
    from furatena.catalog.autodoc_cache import load_cached_autodoc_nodes
    from furatena.catalog.rewrites import load_rewrite_table, set_rewrite_table

    docs_config = load_docs_config(ROOT / "docs.yaml")
    mounts = load_mounts(MOUNTS_CONFIG, repo_root=REPO)
    set_rewrite_table(load_rewrite_table(docs_config.rewrites_path))

    registry = CatalogRegistry.__new__(CatalogRegistry)
    registry.repo_root = REPO
    registry.app_root = ROOT
    registry.rewrites_path = docs_config.rewrites_path
    registry.inventories_path = docs_config.inventories_path
    registry.autodoc_config = AUTODOC_CONFIG
    registry.autodoc_enabled = True
    registry.auto_reload = False
    registry.active_channel = "latest"
    registry.frozen_dir = None
    registry.lazy_html = False
    registry.serve_mode = __import__("catalog.runtime", fromlist=["ServeMode"]).ServeMode.AUTHOR
    registry.mounts = mounts
    registry._html_cache = {}
    registry._shards = {}
    registry._mount_for_url = []
    registry._edges = None
    registry._namespaces = None
    registry._federated_backlinks = {}
    registry._inventory_store = None
    registry._watcher = None
    registry._federated_slug_urls = registry._prescan_federated_slugs()

    cached_autodoc = load_cached_autodoc_nodes(
        config_path=AUTODOC_CONFIG,
        repo_root=REPO,
        frozen_dir=ROOT / "frozen",
    )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _build_mount_shard,
                mount,
                repo_root=REPO,
                app_root=ROOT,
                autodoc_config=AUTODOC_CONFIG,
                federated_slug_urls=registry._federated_slug_urls,
                cached_autodoc=cached_autodoc,
            )
            for mount in mounts
        ]
        for future in as_completed(futures):
            mount_id, shard, _count = future.result()
            registry._shards[mount_id] = shard

    registry._mount_for_url = [(mount.url_prefix or "/", mount) for mount in mounts]
    registry._mount_for_url.sort(key=lambda item: len(item[0]), reverse=True)
    registry._finalize_federated()
    return registry


def _write_frozen_page(*, pages_dir: Path, mount_dir: Path, slug_path: str, html: str, ast_json: str | None) -> None:
    target = pages_dir / f"{slug_path}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html + "\n", encoding="utf-8")
    if ast_json:
        ast_dir = mount_dir / "ast"
        ast_dir.mkdir(parents=True, exist_ok=True)
        ast_target = ast_dir / f"{slug_path}.json"
        ast_target.parent.mkdir(parents=True, exist_ok=True)
        ast_target.write_text(ast_json + "\n", encoding="utf-8")


def _freeze_shard_sequential(registry: CatalogRegistry, mount_id: str, out_dir: Path) -> int:
    shard = registry._shards[mount_id]
    mount_dir = out_dir / "mounts" / mount_id
    pages_dir = mount_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    graph = catalog_graph(shard)
    graph["mount"] = mount_id
    (mount_dir / "catalog.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    for node in shard.nodes:
        slug_path = node.slug or "index"
        html = registry.body_html(node)
        _write_frozen_page(
            pages_dir=pages_dir,
            mount_dir=mount_dir,
            slug_path=slug_path,
            html=html,
            ast_json=node.ast_json,
        )
    return len(shard.nodes)


def _freeze_shard_parallel(registry: CatalogRegistry, mount_id: str, out_dir: Path, workers: int) -> int:
    shard = registry._shards[mount_id]
    mount_dir = out_dir / "mounts" / mount_id
    pages_dir = mount_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)

    graph = catalog_graph(shard)
    graph["mount"] = mount_id
    (mount_dir / "catalog.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    jobs = []
    for node in shard.nodes:
        slug_path = node.slug or "index"
        jobs.append(
            (
                pages_dir,
                mount_dir,
                slug_path,
                registry.body_html(node),
                node.ast_json,
            )
        )

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
    return len(shard.nodes)


def _index_mount_sequential(mount) -> int:
    from furatena.catalog.loader import DocCatalog

    shard = DocCatalog(
        mount.content_root,
        repo_root=REPO,
        autodoc=False,
        mount=mount.id,
        url_prefix=mount.url_prefix,
        source_config=mount.source,
    )
    return len(shard.nodes)


def _index_mount_parallel_pages(mount, workers: int) -> int:
    """Spike: scan once, then adapt pages in a thread pool (per-thread renderer)."""
    from furatena.catalog.context import NodeStub
    from furatena.catalog.graph_schema import infer_section_root
    from furatena.catalog.loader import _build_md_backlinks, _infer_section, _normalize_url
    from furatena.catalog.models import DocNode
    from furatena.catalog.render import DocsRenderer
    from furatena.catalog.sources import FilesystemScanner, PageSource, get_content_adapter

    scanner = FilesystemScanner(mount.source)
    scanned = scanner.scan(mount.content_root, url_prefix=mount.url_prefix)
    slug_to_url = scanner.build_slug_to_url(scanned)
    scanned = scanner.resolve_wikilinks(scanned, slug_to_url, federated_slug_urls={})
    raw_pages = scanner.page_dicts(scanned)

    stubs: dict[str, NodeStub] = {}
    for page in raw_pages:
        meta = page["meta"]
        slug = page["slug"]
        stubs[slug] = NodeStub(
            slug=slug,
            url=page["url"],
            title=str(meta.get("title") or slug.rsplit("/", 1)[-1].replace("-", " ").title()),
            description=str(meta.get("description") or ""),
            weight=int(meta.get("weight") or 100),
            page_type=str(meta.get("type") or meta.get("layout") or "page"),
        )

    valid_urls = {_normalize_url(item["url"]) for item in raw_pages}
    md_backlinks = _build_md_backlinks(
        [(item["url"], item["slug"], stubs[item["slug"]].title, item["body"]) for item in raw_pages],
        valid_urls,
    )

    def adapt_page(page: dict) -> DocNode:
        renderer = DocsRenderer()
        content_format = str(page.get("content_format") or "patitas-markdown")
        adapter = get_content_adapter(content_format, renderer=renderer)

        def render_markdown(
            nested_body: str,
            nested_source_rel: str,
            nested_slug: str,
            nested_stack: set[str],
            nested_depth: int,
        ) -> str:
            nested_renderer = DocsRenderer()
            nested_adapter = get_content_adapter("patitas-markdown", renderer=nested_renderer)
            nested_source = PageSource(
                path=mount.content_root / nested_source_rel,
                content_format="patitas-markdown",
                meta={},
                body=nested_body,
                source_path=nested_source_rel,
                url=slug_to_url.get(nested_slug, f"/{nested_slug}/"),
                slug=nested_slug,
            )
            return nested_adapter.adapt(
                nested_source,
                stubs=stubs,
                render_markdown=render_markdown,
                get_backlinks=lambda url: md_backlinks.get(_normalize_url(url), []),
                content_root=mount.content_root,
                include_stack=nested_stack,
                include_depth=nested_depth,
            ).body_html

        page_source = PageSource(
            path=page["path"],
            content_format=content_format,
            meta=dict(page["meta"]),
            body=page["body"],
            source_path=page["source_path"],
            url=page["url"],
            slug=page["slug"],
        )
        adapted = adapter.adapt(
            page_source,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=lambda url: md_backlinks.get(_normalize_url(url), []),
            content_root=mount.content_root,
        )
        meta = dict(page["meta"])
        slug = page["slug"]
        doc_version = meta.get("doc_version") or meta.get("version")
        if doc_version is not None:
            meta["doc_version"] = str(doc_version)
        view_kind = str(meta.get("layout") or meta.get("kind") or meta.get("type") or "doc")
        return DocNode(
            url=page["url"],
            slug=slug,
            title=stubs[slug].title,
            description=stubs[slug].description,
            layout=view_kind,
            weight=stubs[slug].weight,
            section=str(meta.get("section") or _infer_section(slug)),
            tags=frozenset(meta.get("tags") or ()),
            body_md=page["body"],
            body_html=adapted.body_html,
            toc=adapted.toc,
            source_path=page["source_path"],
            meta=meta,
            mount=mount.id,
            edition="latest",
            section_root=infer_section_root(meta=meta, slug=slug, source_path=page["source_path"]),
            content_ir=adapted.content_ir,
            ast_json=adapted.native_ast,
            content_format=content_format,
            body_text=adapted.body_text,
            sections=adapted.sections,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        nodes = list(pool.map(adapt_page, raw_pages))
    return len(nodes)


def _rerender_page(body_md: str) -> str:
    from furatena.catalog.render import DocsRenderer

    renderer = DocsRenderer()
    document, _ir = renderer.parse(body_md)
    return str(renderer.render_document(document, source=body_md))


def _benchmark_rerender(registry: CatalogRegistry, workers: int) -> tuple[float, float]:
    bodies = [node.body_md for node in registry.nodes if node.body_md][:200]

    start = time.perf_counter()
    for body in bodies:
        _rerender_page(body)
    sequential = time.perf_counter() - start

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_rerender_page, bodies))
    parallel = time.perf_counter() - start
    return sequential, parallel


def _run(label: str, fn) -> Timing:
    start = time.perf_counter()
    detail = fn()
    elapsed = time.perf_counter() - start
    return Timing(label=label, seconds=elapsed, detail=detail or "")


def main() -> None:
    cpus = os.cpu_count() or 4
    worker_grid = sorted({1, 2, max(2, cpus // 2), cpus})

    print(f"Python {sys.version.split()[0]} · GIL {_gil_status()} · {cpus} CPUs")
    print(f"Catalog: {ROOT}")
    print()

    registry = _load_registry()
    page_count = len(registry.nodes)
    mount_counts = {mount.id: len(registry._shards[mount.id].nodes) for mount in registry.mounts}
    print(f"Loaded {page_count} pages: {mount_counts}")
    print()

    results: list[Timing] = []

    mounts = load_mounts(MOUNTS_CONFIG, repo_root=REPO)
    chirp_mount = next(m for m in mounts if m.id == "chirp")

    results.append(_run("Registry load (sequential mounts)", lambda: (_load_registry() and f"{page_count} pages")))

    for workers in worker_grid:
        if workers == 1:
            continue
        label = f"Registry load (parallel mounts, {workers} workers)"
        results.append(
            _run(
                label,
                lambda w=workers: (
                    _parallel_registry_load(w) and f"{page_count} pages across {len(registry.mounts)} mounts"
                ),
            )
        )

    results.append(
        _run(
            "Chirp mount index (sequential pages, no autodoc)",
            lambda: f"{_index_mount_sequential(chirp_mount)} pages",
        )
    )
    best_workers = max(2, cpus // 2)
    results.append(
        _run(
            f"Chirp mount index (parallel pages, {best_workers} workers, no autodoc)",
            lambda: f"{_index_mount_parallel_pages(chirp_mount, best_workers)} pages",
        )
    )

    tmp_base = Path(tempfile.mkdtemp(prefix="fura-spike-"))
    try:
        seq_dir = tmp_base / "sequential"
        seq_dir.mkdir()
        results.append(
            _run(
                "Freeze shard writes (sequential)",
                lambda: f"{_freeze_shard_sequential(registry, 'chirp', seq_dir)} pages",
            )
        )

        for workers in worker_grid:
            par_dir = tmp_base / f"parallel-{workers}"
            par_dir.mkdir()
            label = f"Freeze shard writes ({workers} workers)"
            results.append(
                _run(
                    label,
                    lambda w=workers, d=par_dir: f"{_freeze_shard_parallel(registry, 'chirp', d, w)} pages",
                )
            )

        best_workers = max(2, cpus // 2)
        seq_render, par_render = _benchmark_rerender(registry, best_workers)
        results.append(
            Timing(
                label=f"Markdown re-render sample (200 pages, sequential)",
                seconds=seq_render,
                detail="Patitas parse+render",
            )
        )
        results.append(
            Timing(
                label=f"Markdown re-render sample (200 pages, {best_workers} workers)",
                seconds=par_render,
                detail="Patitas parse+render",
            )
        )
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)

    baseline_registry = results[0].seconds
    baseline_freeze = next(r.seconds for r in results if r.label.startswith("Freeze shard writes (sequential)"))
    print(f"{'Scenario':<52} {'Time':>8}  {'vs baseline':>12}  detail")
    print("-" * 90)
    for row in results:
        baseline = baseline_registry if "Registry" in row.label else baseline_freeze if "Freeze" in row.label else None
        if baseline and row.seconds > 0 and row.label != results[0].label and "sequential" not in row.label.lower():
            delta = f"{baseline / row.seconds:.2f}x"
        elif baseline and "sequential" in row.label.lower():
            delta = "1.00x"
        else:
            delta = ""
        print(f"{row.label:<52} {row.seconds:7.2f}s {delta:>12}  {row.detail}")

    print()
    seq_render_row = next(r for r in results if "re-render sample (200 pages, sequential)" in r.label)
    par_render_row = next(r for r in results if "re-render sample (200 pages," in r.label and "sequential" not in r.label)
    if par_render_row.seconds > 0:
        speedup = seq_render_row.seconds / par_render_row.seconds
        print(
            f"Render CPU sample speedup at {best_workers} workers: {speedup:.2f}x "
            f"({seq_render_row.seconds:.2f}s → {par_render_row.seconds:.2f}s)"
        )


if __name__ == "__main__":
    main()
