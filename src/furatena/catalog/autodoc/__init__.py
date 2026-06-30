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


def _plain_text_from_markdown(body_md: str) -> str:
    return re.sub(r"[#`*_>\[\]()]|https?://\S+", " ", body_md).strip()


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
    """Generate catalog-native API operation nodes from OpenAPI specs."""
    output_prefix = str(openapi_cfg.get("output_prefix") or "api/openapi").strip("/")
    display_name = str(openapi_cfg.get("display_name") or "OpenAPI Reference")
    specs = openapi_cfg.get("specs") or openapi_cfg.get("sources") or ()
    nodes: list[DocNode] = []
    operation_nodes: list[DocNode] = []

    for raw in specs:
        spec_path = _resolve_openapi_spec_path(raw, repo_root=repo_root)
        if spec_path is None:
            continue
        spec = _load_openapi_spec(spec_path)
        if not spec:
            continue
        info = spec.get("info") if isinstance(spec.get("info"), dict) else {}
        spec_title = str(
            info.get("title")
            or (raw.get("title") if isinstance(raw, dict) else "")
            or spec_path.stem.replace("-", " ").title()
        )
        spec_slug = _slugify(spec_title) or spec_path.stem
        spec_prefix = f"{output_prefix}/{spec_slug}".strip("/")
        operation_nodes.extend(
            _openapi_operation_nodes(
                spec,
                spec_path=spec_path,
                spec_prefix=spec_prefix,
                display_name=display_name,
                config=config,
            )
        )

    if not operation_nodes:
        return []

    index_md = "\n".join(
        [
            f"# {display_name}",
            "",
            f"Catalog-native API operation reference ({len(operation_nodes)} operations).",
            "",
            "## Operations",
            "",
            *[
                f"- [{node.title}]({node.url})"
                for node in sorted(operation_nodes, key=lambda item: (item.source_path, item.title))
            ],
        ]
    )
    nodes.append(
        DocNode(
            url=f"/{output_prefix}/",
            slug=output_prefix,
            title=display_name,
            description=f"OpenAPI operation reference ({len(operation_nodes)} operations)",
            layout="api_reference",
            weight=60,
            section=output_prefix.split("/", 1)[0],
            tags=frozenset({"autodoc", "api", "openapi"}),
            body_md=index_md,
            body_html=_render_markdown(index_md),
            toc=_extract_toc(index_md),
            source_path="autodoc:openapi:index",
            meta={"source": "autodoc", "element_type": "api_index", "source_provider": "openapi"},
            content_ir=_parse_autodoc_markdown(index_md),
            body_text=_plain_text_from_markdown(index_md),
        )
    )
    nodes.extend(operation_nodes)
    return nodes


def _resolve_openapi_spec_path(raw: Any, *, repo_root: Path) -> Path | None:
    value = raw.get("path") if isinstance(raw, dict) else raw
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    return path if path.is_file() else None


def _load_openapi_spec(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        import json

        data = json.loads(raw)
    else:
        data = yaml.safe_load(raw)
    return data if isinstance(data, dict) else {}


def _openapi_operation_nodes(
    spec: dict[str, Any],
    *,
    spec_path: Path,
    spec_prefix: str,
    display_name: str,
    config: dict[str, Any],
) -> list[DocNode]:
    nodes: list[DocNode] = []
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    servers = _openapi_environments(spec)
    global_security = _openapi_auth(spec.get("security"))
    for path, item in sorted(paths.items()):
        if not isinstance(item, dict):
            continue
        for method, operation in sorted(item.items()):
            method_l = str(method).lower()
            if method_l not in {"get", "put", "post", "delete", "patch", "options", "head", "trace"}:
                continue
            if not isinstance(operation, dict):
                continue
            operation_id = str(operation.get("operationId") or _operation_id_from_path(method_l, str(path)))
            tags = [str(tag) for tag in operation.get("tags") or [] if str(tag).strip()]
            schemas = sorted(_collect_schema_refs(operation))
            examples = sorted(_collect_example_names(operation))
            request_bodies = sorted(_collect_request_body_names(operation))
            responses = sorted(str(key) for key in operation.get("responses") or {})
            auth = _openapi_auth(operation.get("security")) or global_security
            external_docs = _openapi_external_docs(operation.get("externalDocs"))
            summary = str(operation.get("summary") or operation.get("description") or operation_id).strip()
            description = summary.split("\n", 1)[0][:240]
            api_operation = {
                "operation_id": operation_id,
                "method": method_l.upper(),
                "path": str(path),
                "summary": summary,
                "tags": tags,
                "schemas": schemas,
                "request_bodies": request_bodies,
                "responses": responses,
                "examples": examples,
                "auth": auth,
                "environments": servers,
                "external_docs": external_docs,
                "source_spec": str(spec_path),
            }
            body_md = _openapi_operation_markdown(api_operation, operation)
            slug = f"{spec_prefix}/{_slugify(operation_id) or operation_id.lower()}".strip("/")
            meta = {
                "source": "autodoc",
                "source_provider": "openapi",
                "source_repo": config.get("github_repo"),
                "source_ref": config.get("github_branch"),
                "generated_from": str(spec_path),
                "element_type": "api_operation",
                "operation_id": operation_id,
                "api_operation": api_operation,
                "api_tags": tags,
                "api_schemas": schemas,
                "api_request_bodies": request_bodies,
                "api_responses": responses,
                "api_examples": examples,
                "api_auth": auth,
                "api_environments": servers,
                "implements": f"api:{operation_id}",
            }
            nodes.append(
                DocNode(
                    url=f"/{slug}/",
                    slug=slug,
                    title=f"{method_l.upper()} {path}",
                    description=description,
                    layout="api_reference",
                    weight=120 + len(nodes),
                    section=spec_prefix.split("/", 1)[0],
                    tags=frozenset({"autodoc", "api", "openapi", *[tag.lower() for tag in tags]}),
                    body_md=body_md,
                    body_html=_render_markdown(body_md),
                    toc=_extract_toc(body_md),
                    source_path=str(spec_path),
                    meta=meta,
                    content_ir=_parse_autodoc_markdown(body_md),
                    content_format="openapi-operation",
                    body_text=_plain_text_from_markdown(body_md),
                )
            )
    return nodes


def _operation_id_from_path(method: str, path: str) -> str:
    parts = [method, *re.findall(r"[A-Za-z0-9]+", path)]
    return "-".join(parts)


def _collect_schema_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for item in value.values():
            refs.update(_collect_schema_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_schema_refs(item))
    return refs


def _collect_example_names(operation: dict[str, Any]) -> set[str]:
    names: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            examples = value.get("examples")
            if isinstance(examples, dict):
                names.update(str(key) for key in examples)
            if "example" in value:
                names.add("inline")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(operation)
    return names


def _collect_request_body_names(operation: dict[str, Any]) -> set[str]:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return set()
    refs = _collect_schema_refs(request_body)
    if refs:
        return refs
    return {"requestBody"}


def _openapi_auth(security: Any) -> list[str]:
    schemes: list[str] = []
    if isinstance(security, list):
        for item in security:
            if isinstance(item, dict):
                schemes.extend(str(key) for key in item)
    return sorted(dict.fromkeys(schemes))


def _openapi_environments(spec: dict[str, Any]) -> list[str]:
    servers = spec.get("servers")
    names: list[str] = []
    if isinstance(servers, list):
        for index, server in enumerate(servers, start=1):
            if not isinstance(server, dict):
                continue
            name = str(server.get("description") or server.get("url") or f"server-{index}").strip()
            if name:
                names.append(name)
    return sorted(dict.fromkeys(names))


def _openapi_external_docs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        return []
    url = str(value.get("url") or "").strip()
    if not url:
        return []
    description = str(value.get("description") or "External docs").strip() or "External docs"
    return [{"url": url, "description": description}]


def _openapi_operation_markdown(api_operation: dict[str, Any], operation: dict[str, Any]) -> str:
    lines = [
        f"# {api_operation['method']} {api_operation['path']}",
        "",
        _markdown_safe_text(str(api_operation["summary"])),
        "",
        f"- Operation ID: `{api_operation['operation_id']}`",
    ]
    for label, key in (
        ("Tags", "tags"),
        ("Schemas", "schemas"),
        ("Request bodies", "request_bodies"),
        ("Responses", "responses"),
        ("Examples", "examples"),
        ("Auth", "auth"),
        ("Environments", "environments"),
    ):
        values = api_operation.get(key) or []
        if values:
            lines.append(f"- {label}: " + ", ".join(f"`{value}`" for value in values))
    description = str(operation.get("description") or "").strip()
    if description and description != api_operation["summary"]:
        lines.extend(("", "## Description", "", _markdown_safe_text(description)))
    external_docs = api_operation.get("external_docs") or []
    if external_docs:
        lines.extend(("", "## External docs", ""))
        for item in external_docs:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            label = str(item.get("description") or "External docs").strip() or "External docs"
            if url:
                lines.append(f"- [{_markdown_safe_text(label)}]({url})")
    return "\n".join(lines) + "\n"
