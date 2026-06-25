"""Autodoc slice — ingest API docs from ``config/autodoc.yaml``."""

from __future__ import annotations

import html
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from furatena.catalog.models import ContentIR, DocNode, TocEntry

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$", re.MULTILINE)


def load_autodoc_config(config_path: Path) -> dict[str, Any]:
    """Load autodoc.yaml and return the ``autodoc`` section."""
    if not config_path.is_file():
        return {}
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    section = raw.get("autodoc")
    return section if isinstance(section, dict) else {}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug


def _extract_toc(body_md: str) -> tuple[TocEntry, ...]:
    entries: list[TocEntry] = []
    for match in _HEADING_RE.finditer(body_md):
        depth = len(match.group(1))
        text = match.group(2).strip()
        entries.append(TocEntry(anchor=_slugify(text), text=text, depth=depth))
    return tuple(entries)


def _markdown_safe_text(text: str) -> str:
    """Escape angle brackets in docstring prose so Patitas does not treat them as HTML."""
    return text.strip().replace("<", "&lt;").replace(">", "&gt;")


def _render_markdown(md: str) -> str:
    """Render autodoc markdown with the same Patitas stack as ``DocsRenderer``."""
    try:
        from patitas import Markdown

        return str(Markdown(plugins=["all"], highlight=True)(md))
    except Exception:
        return f"<pre>{html.escape(md)}</pre>"


def _element_to_markdown(element: Any, *, depth: int = 0) -> str:
    """Render a ``DocElement`` subtree as markdown."""
    lines: list[str] = []
    heading = "#" * min(depth + 1, 4)
    title = element.qualified_name or element.name
    lines.append(f"{heading} `{title}`")
    lines.append("")
    if element.description:
        lines.append(_markdown_safe_text(element.description))
        lines.append("")

    if element.element_type in {"function", "method"}:
        signature = (element.metadata or {}).get("signature")
        if signature:
            lines.append("```python")
            lines.append(signature)
            lines.append("```")
            lines.append("")

    for child in element.children or ():
        if child.element_type in {"class", "function", "method", "attribute"}:
            lines.append(_element_to_markdown(child, depth=depth + 1))
    return "\n".join(lines)


def _parse_autodoc_markdown(body_md: str) -> ContentIR:
    """Extract content IR from autodoc markdown without rendering directives."""
    from furatena.catalog.render import DocsRenderer

    _document, content_ir = DocsRenderer().parse(body_md)
    return content_ir


def _module_doc_node(
    element: Any,
    *,
    output_prefix: str,
    display_name: str,
    weight: int,
) -> DocNode:
    slug = f"{output_prefix}/{element.qualified_name.replace('.', '/')}".strip("/")
    url = f"/{slug}/"
    body_md = _element_to_markdown(element)
    body_html = _render_markdown(body_md)
    description = (element.description or "").split("\n", 1)[0].strip()
    return DocNode(
        url=url,
        slug=slug,
        title=element.qualified_name or element.name,
        description=description[:240],
        layout="doc",
        weight=weight,
        section=output_prefix,
        tags=frozenset({"autodoc", "api", display_name.lower().replace(" ", "-")}),
        body_md=body_md,
        body_html=body_html,
        toc=_extract_toc(body_md),
        source_path=str(element.source_file or ""),
        meta={
            "source": "autodoc",
            "element_type": element.element_type,
            "qualified_name": element.qualified_name,
        },
        content_ir=_parse_autodoc_markdown(body_md),
    )


def generate_autodoc_nodes(
    config_path: Path,
    *,
    repo_root: Path | None = None,
    workers: int = 1,
) -> list[DocNode]:
    """Generate catalog nodes from autodoc.yaml (Python slice today)."""
    config = load_autodoc_config(config_path)
    if not config:
        return []

    repo = repo_root or config_path.parent.parent
    nodes: list[DocNode] = []

    python_cfg = config.get("python") or {}
    if python_cfg.get("enabled", True):
        nodes.extend(_python_nodes(python_cfg, repo_root=repo, config=config, workers=workers))

    openapi_cfg = config.get("openapi") or {}
    if openapi_cfg.get("enabled"):
        nodes.extend(_openapi_nodes(openapi_cfg, repo_root=repo, config=config))

    return nodes


_VENV_EXCLUDE_PATTERNS = frozenset({"*/.venv/*", "*/venv/*"})


def _exclude_patterns_for_source(source_dir: str, exclude: list[str]) -> list[str]:
    """Drop venv excludes when scanning an installed ``@package`` tree."""
    raw = str(source_dir).strip()
    if not raw.startswith("@"):
        return exclude
    return [pattern for pattern in exclude if pattern not in _VENV_EXCLUDE_PATTERNS]


def _resolve_source_dir(source_dir: str, *, repo_root: Path) -> Path | None:
    raw = str(source_dir).strip()
    if raw.startswith("@"):
        import importlib.util

        module_name = raw[1:].strip()
        spec = importlib.util.find_spec(module_name)
        if spec is None or not spec.origin:
            return None
        return Path(spec.origin).resolve().parent
    source_path = Path(raw)
    if not source_path.is_absolute():
        source_path = (repo_root / source_path).resolve()
    return source_path if source_path.exists() else None


def _python_nodes(
    python_cfg: dict[str, Any],
    *,
    repo_root: Path,
    config: dict[str, Any],
    workers: int = 1,
) -> list[DocNode]:
    from furatena.catalog.autodoc.extractors.python import PythonExtractor

    output_prefix = str(python_cfg.get("output_prefix") or "api").strip("/")
    display_name = str(config.get("python", {}).get("display_name") or "API Reference")
    exclude = list(python_cfg.get("exclude") or [])

    elements: list[Any] = []
    for source_dir in python_cfg.get("source_dirs") or []:
        source_path = _resolve_source_dir(str(source_dir), repo_root=repo_root)
        if source_path is None:
            continue
        extractor = PythonExtractor(
            exclude_patterns=_exclude_patterns_for_source(str(source_dir), exclude),
            config=python_cfg,
        )
        elements.extend(extractor.extract(source_path))

    modules = [element for element in elements if element.element_type == "module"]
    nodes: list[DocNode] = []
    index_slug = output_prefix
    index_url = f"/{index_slug}/"
    index_md = "\n".join(
        [
            f"# {display_name}",
            "",
            f"Auto-generated from `{python_cfg.get('source_dirs', [''])[0]}`.",
            "",
            "## Modules",
            "",
            *[f"- [{m.qualified_name}](/{output_prefix}/{m.qualified_name.replace('.', '/')}/)"
              for m in sorted(modules, key=lambda m: m.qualified_name)],
        ]
    )
    nodes.append(
        DocNode(
            url=index_url,
            slug=index_slug,
            title=display_name,
            description=f"Python API reference ({len(modules)} modules)",
            layout="doc",
            weight=50,
            section=output_prefix,
            tags=frozenset({"autodoc", "api"}),
            body_md=index_md,
            body_html=_render_markdown(index_md),
            toc=_extract_toc(index_md),
            source_path="autodoc:python:index",
            meta={"source": "autodoc", "element_type": "index"},
            content_ir=_parse_autodoc_markdown(index_md),
        )
    )

    if workers > 1 and len(modules) > 1:
        sorted_modules = sorted(modules, key=lambda m: m.qualified_name)

        def module_node(args: tuple[int, Any]) -> DocNode:
            index, element = args
            return _module_doc_node(
                element,
                output_prefix=output_prefix,
                display_name=display_name,
                weight=100 + index,
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            module_nodes = list(pool.map(module_node, enumerate(sorted_modules)))
        nodes.extend(module_nodes)
    else:
        for index, element in enumerate(sorted(modules, key=lambda m: m.qualified_name)):
            nodes.append(
                _module_doc_node(
                    element,
                    output_prefix=output_prefix,
                    display_name=display_name,
                    weight=100 + index,
                )
            )
    return nodes


def _openapi_nodes(
    openapi_cfg: dict[str, Any],
    *,
    repo_root: Path,
    config: dict[str, Any],
) -> list[DocNode]:
    """OpenAPI autodoc is not implemented in Furatena yet."""
    _ = (openapi_cfg, repo_root, config)
    return []
