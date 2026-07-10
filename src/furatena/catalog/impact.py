"""Stale-content impact reports for CLI, MCP, and CI consumers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from furatena.catalog.export import catalog_graph


def stale_impact_report(
    catalog: Any,
    *,
    slug: Any | None = None,
    stale_public_outputs: tuple[str, ...] = (),
    include_private: bool = True,
) -> dict[str, Any]:
    """Build a structured stale-impact report from live and frozen signals."""
    normalized = str(slug).strip("/") if slug else None
    entries = list(catalog.author_stale_entries(normalized))
    entries.extend(_stale_public_entries(catalog, stale_public_outputs, slug=normalized))
    impact = [
        _stale_impact_entry(catalog, entry, include_private=include_private) for entry in entries
    ]
    output_channel_groups = _group_impact(impact, "output_channel")
    repair_tasks = [_repair_task(item) for item in impact]
    return {
        "schema_version": 1,
        "stale_count": len(entries),
        "entries": entries,
        "impact": impact,
        "repair_tasks": repair_tasks,
        "task_markdown": "\n\n".join(task["markdown"] for task in repair_tasks),
        "groups": {
            "by_owner": _group_impact(impact, "owner"),
            "by_source": _group_impact(impact, "source_key"),
            "by_mount": _group_impact(impact, "mount"),
            "by_tenant": _group_impact(impact, "tenant"),
            "by_workspace": _group_impact(impact, "workspace"),
            "by_site": _group_impact(impact, "site"),
            "by_channel": output_channel_groups,
            "by_output_channel": output_channel_groups,
        },
    }


def _stale_public_entries(
    catalog: Any,
    messages: tuple[str, ...],
    *,
    slug: str | None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for message in messages:
        source_path, _, detail = message.partition(":")
        source_path = source_path.strip()
        node = _node_by_source_path(catalog, source_path)
        if node is not None:
            entry_slug = str(getattr(node, "slug", "") or "").strip("/")
            mount = str(getattr(node, "mount", "") or "")
        else:
            entry_slug = Path(source_path).with_suffix("").as_posix().strip("/")
            mount = ""
        if slug is not None and entry_slug != slug:
            continue
        entries.append(
            {
                "slug": entry_slug,
                "mount": mount,
                "hints": ["export", "agent", "search"],
                "source_path": source_path,
                "reason": detail.strip() or message,
                "mode": "frozen-output",
            }
        )
    return entries


def _stale_impact_entry(
    catalog: Any,
    entry: dict[str, object],
    *,
    include_private: bool,
) -> dict[str, Any]:
    slug = str(entry.get("slug") or "")
    mount = str(entry.get("mount") or "")
    node = _node_for_entry(catalog, slug=slug, mount=mount, source_path=entry.get("source_path"))
    meta = getattr(node, "meta", {}) if node is not None else {}
    owner = _first_meta_value(meta, "owner", "team") or "unassigned"
    provider = _first_meta_value(meta, "source_provider", "provider") or "filesystem"
    repo = _first_meta_value(meta, "source_repo", "repo", "repository")
    ref = _first_meta_value(meta, "source_ref", "ref", "commit", "branch")
    path = getattr(node, "source_path", None) if node is not None else entry.get("source_path")
    source_key = _source_group_key(provider=provider, repo=repo, ref=ref, path=path)
    output_channel = str(
        getattr(catalog, "active_channel", "") or getattr(node, "edition", "") or "default"
    )
    tenant = _first_meta_value(meta, "tenant") or "default"
    workspace = _first_meta_value(meta, "workspace") or "default"
    site = _first_meta_value(meta, "site") or "default"
    refresh_targets = list(entry.get("hints") or ())
    graph_context = _graph_context(catalog, node, include_private=include_private)
    affected_chunks = _affected_chunks(node)
    affected_channels = sorted(
        {
            output_channel,
            *(str(target) for target in refresh_targets if target in {"agent", "export", "search"}),
        }
    )
    return {
        "slug": slug,
        "mount": mount,
        "mode": str(entry.get("mode") or "live-author"),
        "reason": str(entry.get("reason") or "author invalidation is pending"),
        "refresh_targets": refresh_targets,
        "affected_chunks": affected_chunks,
        "changed_graph_edges": graph_context["changed_graph_edges"],
        "graph_context": graph_context,
        "affected_output_channels": affected_channels,
        "owner": owner,
        "source_key": source_key,
        "tenant": tenant,
        "workspace": workspace,
        "site": site,
        "output_channel": output_channel,
        "recommended_remediation": _recommended_remediation(refresh_targets),
        "provenance": {
            "node_id": getattr(node, "node_id", None) if node is not None else None,
            "provider": provider,
            "repo": repo,
            "ref": ref,
            "path": path,
            "mount": mount,
            "edition": getattr(node, "edition", None) if node is not None else None,
            "owner": owner,
            "tenant": tenant,
            "workspace": workspace,
            "site": site,
            "output_channel": output_channel,
        },
    }


def _repair_task(item: dict[str, Any]) -> dict[str, Any]:
    title = f"Refresh stale docs output for {item['slug'] or item['source_key']}"
    source_path = item["provenance"].get("path") or item["source_key"]
    markdown = "\n".join(
        [
            f"### {title}",
            f"- Owner: {item['owner']}",
            f"- Source: {source_path}",
            f"- DCP node: {item['provenance'].get('node_id') or 'unknown'}",
            f"- Refresh targets: {', '.join(item['refresh_targets']) or 'none'}",
            f"- Output channels: {', '.join(item['affected_output_channels']) or item['output_channel']}",
            f"- Recommended remediation: {item['recommended_remediation']}",
        ]
    )
    return {
        "title": title,
        "owner": item["owner"],
        "source_paths": [source_path] if source_path else [],
        "dcp_node_id": item["provenance"].get("node_id"),
        "graph_context": item["graph_context"],
        "recommended_remediation": item["recommended_remediation"],
        "markdown": markdown,
    }


def _node_for_entry(catalog: Any, *, slug: str, mount: str, source_path: Any) -> Any | None:
    if slug and mount:
        node = catalog.get_by_slug(slug, mount=mount)
        if node is not None:
            return node
    if slug:
        node = catalog.get_by_slug(slug)
        if node is not None:
            return node
    if source_path:
        return _node_by_source_path(catalog, str(source_path))
    return None


def _node_by_source_path(catalog: Any, source_path: str) -> Any | None:
    normalized = source_path.strip()
    for node in getattr(catalog, "nodes", ()):
        node_path = str(getattr(node, "source_path", "") or "")
        if node_path == normalized or node_path.endswith(f"/{normalized}"):
            return node
    return None


def _affected_chunks(node: Any | None) -> list[dict[str, Any]]:
    if node is None:
        return []
    chunks = [{"chunk_id": f"{node.node_id}#summary", "heading": "Summary", "url": node.url}]
    for section in getattr(node, "sections", ()) or ():
        chunks.append(
            {
                "chunk_id": f"{node.node_id}#{section.id}",
                "heading": section.heading,
                "url": f"{node.url}#{section.id}",
            }
        )
    return chunks


def _graph_context(catalog: Any, node: Any | None, *, include_private: bool) -> dict[str, Any]:
    if node is None:
        return {"node_id": None, "changed_graph_edges": [], "edge_count": 0}
    graph = catalog_graph(catalog, include_private=include_private)
    node_id = node.node_id
    edges = [
        edge
        for edge in graph.get("edges", [])
        if edge.get("source") == node_id or edge.get("target") == node_id
    ]
    return {
        "node_id": node_id,
        "changed_graph_edges": edges,
        "edge_count": len(edges),
    }


def _recommended_remediation(refresh_targets: list[object]) -> str:
    targets = {str(target) for target in refresh_targets}
    if "export" in targets:
        return "Refresh frozen/public output with fura freeze or fura export --fresh, then rerun fura impact --json."
    if "graph" in targets:
        return "Re-index the catalog graph and rerun semantic impact checks before publishing."
    if "search" in targets or "agent" in targets:
        return "Refresh search and agent export sidecars before publishing or serving MCP context."
    return "Review the source change, refresh affected surfaces, and rerun validation."


def _first_meta_value(meta: Any, *keys: str) -> str | None:
    if not isinstance(meta, dict):
        return None
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _source_group_key(
    *,
    provider: str,
    repo: str | None,
    ref: str | None,
    path: Any,
) -> str:
    parts = [provider]
    if repo:
        parts.append(repo)
    if ref:
        parts[-1] = f"{parts[-1]}@{ref}"
    if path:
        parts.append(str(path))
    return ":".join(parts)


def _group_impact(impact: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in impact:
        key = str(entry.get(field) or "unknown")
        groups.setdefault(key, []).append(entry)
    return [
        {
            "key": key,
            "count": len(items),
            "slugs": sorted(str(item.get("slug") or "") for item in items),
            "refresh_targets": sorted(
                {str(target) for item in items for target in (item.get("refresh_targets") or [])}
            ),
        }
        for key, items in sorted(groups.items())
    ]
