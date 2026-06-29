"""Incremental autodoc — reuse frozen slice when config/sources unchanged."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from furatena.catalog.autodoc import load_autodoc_config
from furatena.catalog.content_ir import content_ir_from_record
from furatena.catalog.models import DocNode, TocEntry


def _file_sig(path: Path) -> str:
    if not path.is_file():
        return ""
    stat = path.stat()
    return f"{path}:{stat.st_mtime_ns}:{stat.st_size}"


def autodoc_fingerprint(config_path: Path, *, repo_root: Path) -> str:
    """Hash autodoc config plus referenced source paths."""
    parts: list[str] = [_file_sig(config_path)]
    config = load_autodoc_config(config_path)
    site_root = config_path.parents[2]

    python_cfg = config.get("python") or {}
    if python_cfg.get("enabled", True):
        for key in ("modules", "packages", "paths"):
            for raw in python_cfg.get(key) or ():
                path = Path(str(raw))
                if not path.is_absolute():
                    path = (repo_root / path).resolve()
                parts.append(_file_sig(path))

    openapi_cfg = config.get("openapi") or {}
    for raw in openapi_cfg.get("specs") or openapi_cfg.get("sources") or ():
        path = Path(str(raw))
        if not path.is_absolute():
            path = (site_root / path).resolve()
        parts.append(_file_sig(path))

    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest[:16]


def write_autodoc_fingerprint(frozen_dir: Path, fingerprint: str) -> None:
    path = frozen_dir / "autodoc.fingerprint"
    path.write_text(fingerprint + "\n", encoding="utf-8")


def read_autodoc_fingerprint(frozen_dir: Path) -> str | None:
    path = frozen_dir / "autodoc.fingerprint"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def autodoc_nodes_from_frozen(frozen_dir: Path, *, mount: str) -> list[DocNode]:
    """Load autodoc nodes from a frozen mount shard."""
    mount_dir = frozen_dir / "mounts" / mount
    graph_path = mount_dir / "catalog.json"
    pages_dir = mount_dir / "pages"
    if not graph_path.is_file():
        return []
    raw = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes: list[DocNode] = []
    for page in raw.get("pages") or []:
        if not isinstance(page, dict):
            continue
        if page.get("source") != "autodoc":
            continue
        slug = str(page.get("slug") or "")
        meta = {
            "source": "autodoc",
            "element_type": page.get("element_type"),
            "qualified_name": page.get("qualified_name"),
            "api_operation": page.get("api_operation"),
            "source_provider": page.get("source_provider"),
            "source_repo": page.get("source_repo"),
            "source_ref": page.get("source_ref"),
            "generated_from": page.get("generated_from"),
        }
        api_operation = page.get("api_operation")
        if isinstance(api_operation, dict):
            operation_id = api_operation.get("operation_id")
            meta.update(
                {
                    "operation_id": operation_id,
                    "api_tags": api_operation.get("tags") or [],
                    "api_schemas": api_operation.get("schemas") or [],
                    "api_request_bodies": api_operation.get("request_bodies") or [],
                    "api_responses": api_operation.get("responses") or [],
                    "api_examples": api_operation.get("examples") or [],
                    "api_auth": api_operation.get("auth") or [],
                    "api_environments": api_operation.get("environments") or [],
                }
            )
            if operation_id:
                meta["implements"] = f"api:{operation_id}"
        slug_path = slug or "index"
        html_rel = f"{slug_path}.html"
        html_path_ref = html_rel if (pages_dir / html_rel).is_file() else None
        toc_raw = page.get("toc") or []
        toc = tuple(
            TocEntry(anchor=e["anchor"], text=e["text"], depth=int(e["depth"]))
            for e in toc_raw
            if isinstance(e, dict)
        )
        content_ir = content_ir_from_record(page.get("content"))
        nodes.append(
            DocNode(
                url=page["url"],
                slug=slug,
                title=page.get("title", ""),
                description=page.get("description", ""),
                layout=str(page.get("layout") or "doc"),
                weight=int(page.get("weight") or 100),
                section=str(page.get("section") or ""),
                tags=frozenset(page.get("tags") or ()),
                body_md=str(page.get("body_md") or ""),
                body_html="",
                toc=toc,
                source_path=str(page.get("source_path") or ""),
                meta=meta,
                mount=mount,
                edition=str(page.get("edition") or "latest"),
                section_root=bool(page.get("section_root")),
                html_path=html_path_ref,
                content_ir=content_ir,
                content_format=str(page.get("content_format") or "patitas-markdown"),
                body_text=str(page.get("body_text") or ""),
            )
        )
    return nodes


def load_cached_autodoc_nodes(
    *,
    config_path: Path | None,
    repo_root: Path,
    frozen_dir: Path | None,
    mount: str = "chirp",
) -> list[DocNode] | None:
    """Return frozen autodoc nodes when fingerprint matches; else None."""
    if config_path is None or frozen_dir is None:
        return None
    expected = read_autodoc_fingerprint(frozen_dir)
    if not expected:
        return None
    if autodoc_fingerprint(config_path, repo_root=repo_root) != expected:
        return None
    nodes = autodoc_nodes_from_frozen(frozen_dir, mount=mount)
    return nodes or None
