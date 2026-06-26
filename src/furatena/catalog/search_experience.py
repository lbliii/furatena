"""Search UX helpers for catalog-native hybrid search (no Lunr)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from furatena.catalog.semantic import HybridHit, HybridSearchResult, hybrid_search

if TYPE_CHECKING:
    from furatena.catalog.embeddings import EmbeddingIndex, SemanticHit
    from furatena.catalog.registry import CatalogRegistry


@dataclass(frozen=True, slots=True)
class SearchHitGroup:
    """Grouped search hits for the full-page catalog search experience."""

    label: str
    hits: tuple[HybridHit, ...]


@dataclass(frozen=True, slots=True)
class SearchSpotlight:
    """Command-strip metrics for the layered catalog search shell."""

    scope_label: str
    kicker: str
    title: str
    result_count: int
    match_count: int
    visible_pages: int
    section_count: int
    top_tags: tuple[str, ...]
    top_sections: tuple[str, ...]
    scoped: bool
    global_search: bool
    workspace_kicker: str
    workspace_title: str
    workspace_subtitle: str


@dataclass(frozen=True, slots=True)
class SearchPageMatch:
    """One chunk-level deep link on a workspace product card."""

    label: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchPageCard:
    """Aggregated page result with multiple chunk matches (Fern product-card shape)."""

    node: object
    score: float
    snippet: str
    matches: tuple[SearchPageMatch, ...]
    tag_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchCatalogSnapshot:
    """One catalog pass for rails, facets, spotlight, and browse cards."""

    nodes: tuple[object, ...]
    page_count: int
    mount_labels: dict[str, str]

    def mount_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes:
            mount_id = getattr(node, "mount", None) or "chirp"
            counts[mount_id] = counts.get(mount_id, 0) + 1
        return counts

    def section_counts(self, *, mount: str = "") -> dict[str, int]:
        counts: dict[str, int] = {}
        for node in self.nodes:
            if mount and getattr(node, "mount", "") != mount:
                continue
            label = (getattr(node, "section", "") or "").strip() or "Documentation"
            counts[label] = counts.get(label, 0) + 1
        return counts

    def section_names(self) -> tuple[str, ...]:
        return tuple(sorted({node.section for node in self.nodes if getattr(node, "section", "")}))

    def filtered(
        self,
        *,
        mount: str = "",
        section: str = "",
        tag: str = "",
        channel: str = "latest",
    ) -> tuple[object, ...]:
        selected: list[object] = []
        for node in self.nodes:
            if mount and getattr(node, "mount", "") != mount:
                continue
            if section and getattr(node, "section", "") != section:
                continue
            if tag and tag not in getattr(node, "tags", ()):
                continue
            if channel and channel != "latest" and getattr(node, "edition", "") != channel:
                continue
            selected.append(node)
        return tuple(selected)


def build_search_catalog_snapshot(catalog: CatalogRegistry) -> SearchCatalogSnapshot:
    """Merge catalog doc nodes once per search request."""
    nodes = tuple(catalog.doc_nodes())
    mount_labels = {mount.id: mount.label for mount in catalog.mounts}
    return SearchCatalogSnapshot(
        nodes=nodes,
        page_count=len(nodes),
        mount_labels=mount_labels,
    )


def build_search_url(
    *,
    query: str = "",
    section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "",
    lang: str = "",
    global_search: bool = False,
) -> str:
    from urllib.parse import urlencode

    params: dict[str, str] = {}
    if mount:
        params["mount"] = mount
    if section:
        params["section"] = section
    if tag:
        params["tag"] = tag
    if channel and channel != "latest":
        params["channel"] = channel
    if lang:
        params["lang"] = lang
    if query:
        params["q"] = query
    if query and global_search:
        params["global"] = "1"
    query_string = urlencode(params)
    return f"/search?{query_string}" if query_string else "/search"


def scope_mark(label: str) -> str:
    """Short rail glyph for a catalog section (Fern family-rail analogue)."""
    words = [part for part in label.replace("-", " ").split() if part]
    if len(words) >= 2:
        return "".join(word[0] for word in words[:3]).upper()[:3]
    compact = label.replace(" ", "")
    return compact[:3].upper() or "DOC"


def rail_mark(label: str, value: str = "") -> str:
    """Rail badge for scope/mount items — precomputed for templates as ``item.mark``."""
    if not value and label.lower().startswith("all"):
        return "ALL"
    source = value if value and value != label else label
    return scope_mark(source)


def search_nav_attrs(href: str) -> dict[str, str]:
    """HTMX attrs for in-search navigation that opts out of shell boost."""
    return {
        "hx-get": href,
        "hx-target": "#search-results-panel",
        "hx-push-url": "true",
        "hx-disinherit": "hx-select hx-target hx-swap",
        "hx-select": "#search-results-panel",
    }


def search_mount_rail_items(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    active_mount: str = "",
    query: str = "",
    section: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
) -> list[dict[str, str | int | bool | dict[str, str]]]:
    """L1 area rail — documentation mounts with live page counts."""
    snap = snapshot or build_search_catalog_snapshot(catalog)
    counts = snap.mount_counts()
    labels = snap.mount_labels

    total = sum(counts.values())
    filters = {
        "query": query,
        "section": section,
        "tag": tag,
        "channel": channel,
        "global_search": global_search,
    }
    all_href = build_search_url(**filters)
    items: list[dict[str, str | int | bool | dict[str, str]]] = [
        {
            "label": "All mounts",
            "value": "",
            "mark": rail_mark("All mounts"),
            "count": total,
            "href": all_href,
            "nav_attrs": search_nav_attrs(all_href),
            "active": not active_mount,
        }
    ]
    for mount_id in sorted(counts):
        href = build_search_url(mount=mount_id, **filters)
        items.append(
            {
                "label": labels.get(mount_id, mount_id),
                "value": mount_id,
                "mark": rail_mark(labels.get(mount_id, mount_id), mount_id),
                "count": counts[mount_id],
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_mount == mount_id,
            }
        )
    return items


def search_scope_rail_items(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    active_section: str = "",
    active_mount: str = "",
    query: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
) -> list[dict[str, str | int | bool | dict[str, str]]]:
    """L2 family rail — catalog sections with live page counts."""
    snap = snapshot or build_search_catalog_snapshot(catalog)
    counts = snap.section_counts(mount=active_mount)

    total = sum(counts.values())
    filters = {
        "query": query,
        "mount": active_mount,
        "tag": tag,
        "channel": channel,
        "global_search": global_search,
    }
    all_href = build_search_url(**filters)
    items: list[dict[str, str | int | bool | dict[str, str]]] = [
        {
            "label": "All sections",
            "value": "",
            "mark": rail_mark("All sections"),
            "count": total,
            "href": all_href,
            "nav_attrs": search_nav_attrs(all_href),
            "active": not active_section,
        }
    ]
    for label in sorted(counts):
        href = build_search_url(section=label, **filters)
        items.append(
            {
                "label": label,
                "value": label,
                "mark": rail_mark(label, label),
                "count": counts[label],
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_section == label,
            }
        )
    return items


def search_discovery_sections(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    scope_rail: list[dict[str, str | int | bool | dict[str, str]]] | None = None,
    query: str = "",
    active_mount: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
    limit: int = 8,
) -> list[dict[str, str | int | dict[str, str]]]:
    """Section entry tiles for browse / empty-state discovery."""
    items = scope_rail or search_scope_rail_items(
        catalog,
        snapshot=snapshot,
        active_section="",
        active_mount=active_mount,
        query=query,
        tag=tag,
        channel=channel,
        global_search=global_search,
    )
    sections = [item for item in items if item.get("value")]
    return sections[:limit]


def search_topic_links(
    tags: tuple[str, ...] | list[str],
    *,
    query: str = "",
    section: str = "",
    mount: str = "",
    channel: str = "",
    active_tag: str = "",
    global_search: bool = False,
    limit: int = 8,
) -> list[dict[str, str | bool | dict[str, str]]]:
    """Topic filter pills for the workspace header."""
    links: list[dict[str, str | bool | dict[str, str]]] = []
    for tag in tags[:limit]:
        href = build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        )
        links.append(
            {
                "label": tag,
                "value": tag,
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_tag == tag,
            }
        )
    return links


def search_edition_links(
    channels: tuple[object, ...] | list[object],
    *,
    active_channel: str = "",
    query: str = "",
    section: str = "",
    mount: str = "",
    tag: str = "",
    global_search: bool = False,
) -> list[dict[str, str | bool | dict[str, str]]]:
    """Edition strip pills scoped to the search workspace."""
    links: list[dict[str, str | bool | dict[str, str]]] = []
    for channel in channels:
        channel_id = getattr(channel, "id", str(channel))
        label = getattr(channel, "label", channel_id)
        href = build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel_id,
            global_search=global_search,
        )
        links.append(
            {
                "label": label,
                "value": channel_id,
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_channel == channel_id,
            }
        )
    return links


def search_result_section_links(
    groups: list[SearchHitGroup],
    *,
    query: str = "",
    active_section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
) -> list[dict[str, str | int | bool | dict[str, str]]]:
    """Jump links for result groups in the discovery aside."""
    links: list[dict[str, str | int | bool | dict[str, str]]] = []
    for group in groups:
        section = group.label if group.label != "Documentation" else ""
        href = build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        )
        links.append(
            {
                "label": group.label,
                "count": len(group.hits),
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_section == section,
            }
        )
    return links


def search_spotlight_stats(
    catalog: CatalogRegistry,
    hits: list[HybridHit],
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    query: str = "",
    section: str = "",
    mount: str = "",
    tag: str = "",
    global_search: bool = False,
) -> SearchSpotlight:
    snap = snapshot or build_search_catalog_snapshot(catalog)
    scoped_nodes = list(
        snap.filtered(mount=mount, section=section, tag=tag)
    )
    hit_nodes = [hit.node for hit in hits]
    tags: dict[str, int] = {}
    sections: dict[str, int] = {}
    for node in hit_nodes or scoped_nodes:
        for node_tag in node.tags:
            tags[node_tag] = tags.get(node_tag, 0) + 1
        label = node.section.strip() or "Documentation"
        sections[label] = sections.get(label, 0) + 1

    top_tags = tuple(tag_name for tag_name, _ in sorted(tags.items(), key=lambda item: (-item[1], item[0]))[:8])
    top_sections = tuple(
        label for label, _ in sorted(sections.items(), key=lambda item: (-item[1], item[0]))[:4]
    )

    scoped = bool(section or mount or tag) and not global_search
    if query and global_search:
        scope_label = "All catalog sections"
    elif section:
        scope_label = section
    elif mount:
        mount_label = snapshot.mount_labels.get(mount, mount)
        scope_label = mount_label
    else:
        scope_label = "All catalog sections"

    if query:
        kicker = "Search results"
        title = f"“{query}”"
        workspace_kicker = (section or scope_label).upper()
        workspace_title = f"Results for “{query}”"
    elif section:
        kicker = "Catalog scope"
        title = section
        workspace_kicker = section.upper()
        workspace_title = f"{section} docs"
    elif mount:
        mount_label = snapshot.mount_labels.get(mount, mount)
        kicker = "Catalog scope"
        title = mount_label
        workspace_kicker = mount.upper()
        workspace_title = mount_label
    else:
        kicker = "Catalog scope"
        title = "All documentation"
        workspace_kicker = "ALL CATALOG"
        workspace_title = "Documentation workspace"

    subtitle_parts = []
    if top_sections:
        subtitle_parts.extend(top_sections[:3])
    elif tag:
        subtitle_parts.append(tag)
    workspace_subtitle = ", ".join(subtitle_parts)

    unique_pages = len({node.node_id for node in hit_nodes}) if hit_nodes else len(scoped_nodes)

    return SearchSpotlight(
        scope_label=scope_label,
        kicker=kicker,
        title=title,
        result_count=len(hits),
        match_count=len(hits),
        visible_pages=unique_pages,
        section_count=len(sections) if sections else len({node.section for node in scoped_nodes if node.section}),
        top_tags=top_tags,
        top_sections=top_sections,
        scoped=scoped,
        global_search=global_search,
        workspace_kicker=workspace_kicker,
        workspace_title=workspace_title,
        workspace_subtitle=workspace_subtitle,
    )


def hybrid_search_hits(
    catalog: CatalogRegistry,
    index: EmbeddingIndex,
    query: str,
    *,
    limit: int = 12,
    section: str | None = None,
    mount: str | None = None,
    tag: str | None = None,
    channel: str | None = None,
    lang: str | None = None,
    global_search: bool = False,
    documents: dict[str, object] | None = None,
) -> HybridSearchResult:
    """Rank pages with keyword + TF-IDF chunk retrieval."""
    return hybrid_search(
        catalog,
        index,
        query,
        limit=limit,
        section=None if global_search else section,
        mount=None if global_search else mount,
        tag=None if global_search else tag,
        edition=None if global_search or not channel or channel == "latest" else channel,
        lang=lang,
        semantic_limit=max(limit * 3, 64),
        documents=documents,
    )


def build_search_page_cards(
    hits: list[HybridHit],
    semantic_hits: list[SemanticHit] | tuple[SemanticHit, ...],
    query: str,
    *,
    index: EmbeddingIndex | None = None,
    matches_per_page: int = 3,
) -> list[SearchPageCard]:
    """Aggregate hybrid hits into Fern-style product cards with chunk footers."""
    semantic_by_node: dict[str, list[SemanticHit]] = {}
    for sem_hit in semantic_hits:
        semantic_by_node.setdefault(sem_hit.chunk.node_id, []).append(sem_hit)

    grouped: dict[str, list[HybridHit]] = {}
    order: list[str] = []
    for hit in hits:
        if hit.node.node_id not in grouped:
            grouped[hit.node.node_id] = []
            order.append(hit.node.node_id)
        grouped[hit.node.node_id].append(hit)

    cards: list[SearchPageCard] = []
    for node_id in order:
        node_hits = grouped[node_id]
        node = node_hits[0].node
        best = max(node_hits, key=lambda hit: hit.score)
        matches: list[SearchPageMatch] = []
        seen_urls: set[str] = set()

        def _append_match(
            label: str,
            url: str,
            snippet: str,
            *,
            page_matches: list[SearchPageMatch] = matches,
            page_seen_urls: set[str] = seen_urls,
        ) -> None:
            if url in page_seen_urls:
                return
            page_matches.append(SearchPageMatch(label=label, url=url, snippet=snippet[:180]))
            page_seen_urls.add(url)

        for hit in node_hits:
            url = search_hit_url(hit, index)
            heading = search_hit_heading(hit, index) or hit.node.title
            _append_match(heading, url, hit.snippet or hit.node.description)

        if query and len(matches) < matches_per_page:
            for sem_hit in semantic_by_node.get(node_id, ()):
                chunk = sem_hit.chunk
                _append_match(chunk.heading or chunk.title, chunk.url, chunk.text)
                if len(matches) >= matches_per_page:
                    break

        cards.append(
            SearchPageCard(
                node=node,
                score=best.score,
                snippet=best.snippet or node.description,
                matches=tuple(matches[:matches_per_page]),
                tag_labels=tuple(sorted(node.tags)[:4]),
            )
        )
    cards.sort(key=lambda card: (-card.score, getattr(card.node, "title", "").lower()))
    return cards


def chunk_for_hit(hit: HybridHit, index: EmbeddingIndex | None) -> object | None:
    if index is None or not hit.chunk_id:
        return None
    return index.get_chunk(hit.chunk_id)


def search_hit_url(hit: HybridHit, index: EmbeddingIndex | None = None) -> str:
    chunk = chunk_for_hit(hit, index)
    if chunk is not None:
        if "#" in chunk.url:
            return chunk.url
        if hit.chunk_id and "#" in hit.chunk_id:
            anchor = hit.chunk_id.rsplit("#", 1)[1]
            if anchor and anchor != "summary":
                base = chunk.url if chunk.url.endswith("/") else f"{chunk.url}/"
                return f"{base.rstrip('/')}#{anchor}"
        return chunk.url
    return hit.node.url


def search_hit_heading(hit: HybridHit, index: EmbeddingIndex | None = None) -> str | None:
    chunk = chunk_for_hit(hit, index)
    if chunk is None:
        return None
    heading = chunk.heading.strip()
    if not heading or heading == hit.node.title:
        return None
    return heading


def search_section_options(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
) -> list[dict[str, str]]:
    snap = snapshot or build_search_catalog_snapshot(catalog)
    return [{"value": section, "label": section} for section in snap.section_names()]


def search_facet_links(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    query: str = "",
    active_section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
) -> list[dict[str, str | bool | dict[str, str]]]:
    """Pre-built facet nav links for sidebar HTMX filtering."""
    snap = snapshot or build_search_catalog_snapshot(catalog)
    all_href = build_search_url(
        query=query,
        mount=mount,
        tag=tag,
        channel=channel,
        global_search=global_search,
    )
    links: list[dict[str, str | bool | dict[str, str]]] = [
        {
            "label": "All sections",
            "value": "",
            "href": all_href,
            "nav_attrs": search_nav_attrs(all_href),
            "active": not active_section,
        }
    ]
    for option in search_section_options(catalog, snapshot=snap):
        href = build_search_url(
            query=query,
            section=option["value"],
            mount=mount,
            tag=tag,
            channel=channel,
        )
        links.append(
            {
                "label": option["label"],
                "value": option["value"],
                "href": href,
                "nav_attrs": search_nav_attrs(href),
                "active": active_section == option["value"],
            }
        )
    return links


def search_popular_links(
    *,
    queries: tuple[str, ...] = ("htmx", "installation", "streaming"),
    section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "",
    global_search: bool = False,
) -> list[dict[str, str | dict[str, str]]]:
    """Pre-built popular-query pills for the search empty state."""
    links: list[dict[str, str | dict[str, str]]] = []
    for query in queries:
        href = build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        )
        links.append(
            {
                "label": query,
                "href": href,
                "nav_attrs": search_nav_attrs(href),
            }
        )
    return links


def search_aside_links(
    *,
    queries: tuple[str, ...] = ("htmx", "hypermedia", "streaming"),
    section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "",
) -> list[dict[str, str | dict[str, str]]]:
    """Pre-built aside suggestion pills when a query is active."""
    links: list[dict[str, str | dict[str, str]]] = []
    for query in queries:
        href = build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
        )
        links.append(
            {
                "label": query,
                "href": href,
                "nav_attrs": search_nav_attrs(href),
            }
        )
    return links


def build_search_browse_cards(
    catalog: CatalogRegistry,
    *,
    snapshot: SearchCatalogSnapshot | None = None,
    mount: str = "",
    section: str = "",
    tag: str = "",
    channel: str = "",
    limit: int = 12,
) -> list[SearchPageCard]:
    """Scoped catalog cards for the empty-query workspace browse grid."""
    snap = snapshot or build_search_catalog_snapshot(catalog)
    nodes = snap.filtered(mount=mount, section=section, tag=tag, channel=channel)
    cards: list[SearchPageCard] = []
    for node in nodes[:limit]:
        cards.append(
            SearchPageCard(
                node=node,
                score=0.0,
                snippet=node.description or "",
                matches=(),
                tag_labels=tuple(sorted(node.tags)[:4]),
            )
        )
    return cards


def build_search_workspace_context(
    catalog: CatalogRegistry,
    index: EmbeddingIndex,
    *,
    query: str = "",
    section: str = "",
    mount: str = "",
    tag: str = "",
    channel: str = "latest",
    lang: str = "",
    global_search: bool = False,
    limit: int = 24,
    include_shell_extras: bool = True,
) -> dict[str, object]:
    """Build search workspace template context with one catalog snapshot and one hybrid query."""
    snapshot = build_search_catalog_snapshot(catalog)
    documents = catalog.ast_documents()
    effective_lang = lang or None
    if effective_lang is None and catalog.i18n_config.enabled:
        effective_lang = catalog.i18n_config.default_language
    search_result = (
        hybrid_search_hits(
            catalog,
            index,
            query,
            limit=limit,
            section=section or None,
            mount=mount or None,
            tag=tag or None,
            channel=channel or None,
            lang=effective_lang,
            global_search=global_search,
            documents=documents,
        )
        if query
        else None
    )
    hits = list(search_result.hits) if search_result is not None else []
    hit_groups = group_search_hits(hits)
    if query and search_result is not None:
        page_cards = build_search_page_cards(
            hits,
            search_result.semantic_hits,
            query,
            index=index,
        )
    else:
        page_cards = build_search_browse_cards(
            catalog,
            snapshot=snapshot,
            mount=mount,
            section=section,
            tag=tag,
            channel=channel,
            limit=limit,
        )

    scope_rail = search_scope_rail_items(
        catalog,
        snapshot=snapshot,
        active_section=section,
        active_mount=mount,
        query=query,
        tag=tag,
        channel=channel,
        global_search=global_search,
    )
    mount_rail = search_mount_rail_items(
        catalog,
        snapshot=snapshot,
        active_mount=mount,
        query=query,
        section=section,
        tag=tag,
        channel=channel,
        global_search=global_search,
    )
    spotlight = search_spotlight_stats(
        catalog,
        hits,
        snapshot=snapshot,
        query=query,
        section=section,
        mount=mount,
        tag=tag,
        global_search=global_search,
    )
    expand_url = (
        build_search_url(
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=True,
        )
        if query
        else ""
    )

    ctx: dict[str, object] = {
        "hits": hits,
        "search_hit_groups": hit_groups,
        "search_page_cards": page_cards,
        "search_result_section_links": search_result_section_links(
            hit_groups,
            query=query,
            active_section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        ),
        "search_discovery_sections": search_discovery_sections(
            catalog,
            snapshot=snapshot,
            scope_rail=scope_rail,
            query=query,
            active_mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        ),
        "results": [hit.node for hit in hits],
        "search_query": query,
        "search_section": section,
        "search_mount": mount,
        "search_tag": tag,
        "search_channel": channel,
        "search_global": global_search,
        "search_global_expand_url": expand_url,
        "search_global_expand_nav_attrs": (
            search_nav_attrs(expand_url) if expand_url else {}
        ),
        "search_reset_nav_attrs": search_nav_attrs("/search"),
        "search_popular_links": search_popular_links(
            section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        ),
        "search_aside_links": (
            search_aside_links(
                section=section,
                mount=mount,
                tag=tag,
                channel=channel,
            )
            if query
            else []
        ),
        "search_scope_rail": scope_rail,
        "search_mount_rail": mount_rail,
        "search_topic_links": search_topic_links(
            spotlight.top_tags,
            query=query,
            section=section,
            mount=mount,
            channel=channel,
            active_tag=tag,
            global_search=global_search,
        ),
        "search_edition_links": search_edition_links(
            catalog.channels,
            active_channel=channel,
            query=query,
            section=section,
            mount=mount,
            tag=tag,
            global_search=global_search,
        ),
        "search_spotlight": spotlight,
        "page_count": snapshot.page_count,
    }
    if include_shell_extras:
        ctx["search_sections"] = search_section_options(catalog, snapshot=snapshot)
        ctx["search_facet_links"] = search_facet_links(
            catalog,
            snapshot=snapshot,
            query=query,
            active_section=section,
            mount=mount,
            tag=tag,
            channel=channel,
            global_search=global_search,
        )
    else:
        ctx["search_sections"] = []
        ctx["search_facet_links"] = []
    return ctx


def search_lint_context(catalog: CatalogRegistry) -> dict[str, object]:
    """Minimal search-shell context for ``fura check`` smoke renders."""
    from furatena.catalog.embeddings import EmbeddingIndex

    index = EmbeddingIndex.from_nodes(list(catalog.nodes), documents=catalog.ast_documents())
    ctx = build_search_workspace_context(
        catalog,
        index,
        query="htmx",
        include_shell_extras=True,
        limit=6,
    )
    ctx["search_oob"] = False
    return ctx


def group_search_hits(hits: list[HybridHit]) -> list[SearchHitGroup]:
    """Group ranked hits by catalog section for the dedicated search page."""
    groups: dict[str, list[HybridHit]] = {}
    order: list[str] = []
    for hit in hits:
        label = hit.node.section.strip() or "Documentation"
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(hit)
    return [SearchHitGroup(label=label, hits=tuple(groups[label])) for label in order]


def highlight_search_terms(text: str, query: str) -> str:
    """Wrap query term matches in ``<mark>`` for search result snippets."""
    import html
    import re

    if not text or not query.strip():
        return html.escape(text)

    safe = html.escape(text)
    terms = sorted(
        {t for t in re.findall(r"\w+", query.lower()) if len(t) > 1},
        key=len,
        reverse=True,
    )
    if not terms:
        terms = [query.strip().lower()]

    highlighted = safe
    for term in terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)

        def _mark(match: re.Match[str]) -> str:
            return f"<mark>{match.group(0)}</mark>"

        highlighted = pattern.sub(_mark, highlighted)
    return highlighted
