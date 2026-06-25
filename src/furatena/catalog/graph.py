"""Doc graph helpers — backlinks and link extraction."""

from __future__ import annotations

from furatena.catalog.models import DocNode


def normalize_internal_url(href: str) -> str | None:
    """Normalize an internal docs href, or return None when external/invalid."""
    from html import unescape

    from furatena.catalog.directives.html import rewrite_href

    if not href or not href.startswith("/") or href.startswith("//"):
        return None
    cleaned = rewrite_href(unescape(href.split("#", 1)[0].split("?", 1)[0].strip()))
    if not cleaned:
        return None
    return cleaned if cleaned.endswith("/") or cleaned == "/" else f"{cleaned}/"


def extract_page_links(node: DocNode, catalog=None) -> set[str]:
    """Collect internal links from Content IR (no HTML regex)."""
    from furatena.catalog.content_ir import collect_node_link_urls

    return collect_node_link_urls(node, catalog=catalog)


def build_backlinks(nodes: list[DocNode], catalog=None) -> dict[str, list[dict[str, str]]]:
    """Map target URL → referring pages within one shard (same mount + edition)."""
    return build_federated_backlinks(nodes, catalog=catalog)


def build_federated_backlinks(nodes: list[DocNode], catalog=None) -> dict[str, list[dict[str, str]]]:
    """Map target URL → referring pages across federated mounts (same edition)."""
    by_url = {node.url: node for node in nodes}
    incoming: dict[str, list[dict[str, str]]] = {}
    for node in nodes:
        targets = extract_page_links(node, catalog=catalog)
        for target in targets:
            if target == node.url:
                continue
            target_node = by_url.get(target)
            if target_node is None:
                continue
            if target_node.edition != node.edition:
                continue
            incoming.setdefault(target, []).append(
                {"title": node.title, "href": node.url},
            )
    for target, refs in incoming.items():
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for ref in refs:
            if ref["href"] in seen:
                continue
            seen.add(ref["href"])
            deduped.append(ref)
        incoming[target] = sorted(deduped, key=lambda r: r["title"].lower())
    return incoming
