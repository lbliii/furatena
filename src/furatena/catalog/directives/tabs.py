"""Tab-set directive — Alpine + chirp-ui tabs, chirp-theme visual skin."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.contracts import DirectiveContract
from patitas.directives.options import StyledOptions
from patitas.nodes import Directive

from furatena.catalog.directives.html import render_inline_text
from furatena.catalog.directives.kida_render import as_markup, render_doc_tabs

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

TAB_SET_CONTRACT = DirectiveContract(
    requires_children=("tab-item",),
    allows_children=("tab-item",),
)
TAB_ITEM_CONTRACT = DirectiveContract(requires_parent=("tab-set",))

_TAB_BOUNDARY = "<!-- fura-tab-item -->"
_TAB_BOUNDARY_RE = re.compile(
    rf"{re.escape(_TAB_BOUNDARY)}(.*?){re.escape(_TAB_BOUNDARY)}",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class TabSetOptions(StyledOptions):
    id: str | None = None
    sync: str | None = None


@dataclass(frozen=True, slots=True)
class TabItemOptions(StyledOptions):
    selected: bool = False
    icon: str | None = None
    badge: str | None = None
    disabled: bool = False


@dataclass
class _TabItemData:
    title: str
    selected: bool
    icon: str
    badge: str
    disabled: bool
    content: str


def _slug(text: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")
    return slug or "tab"


def _split_tab_contents(rendered_children: str) -> list[str]:
    """Split rendered tab-item bodies on boundary markers."""
    if _TAB_BOUNDARY not in rendered_children:
        return []
    chunks = rendered_children.split(_TAB_BOUNDARY)
    contents = [chunks[index].strip() for index in range(1, len(chunks), 2)]
    if contents:
        return contents
    return [part.strip() for part in _TAB_BOUNDARY_RE.findall(rendered_children)]


def _tab_items_from_ast(
    node: Directive[Any],
    rendered_children: str,
) -> list[_TabItemData]:
    """Collect tab metadata from AST children and pair with rendered bodies."""
    child_nodes = [
        child
        for child in node.children
        if isinstance(child, Directive) and child.name == "tab-item"
    ]
    contents = _split_tab_contents(rendered_children)
    if not child_nodes:
        return []
    if len(contents) < len(child_nodes):
        contents.extend([""] * (len(child_nodes) - len(contents)))

    items: list[_TabItemData] = []
    for child, content in zip(child_nodes, contents, strict=True):
        opts = child.options
        if opts.disabled:
            continue
        items.append(
            _TabItemData(
                title=child.title or "Tab",
                selected=opts.selected,
                icon=opts.icon or "",
                badge=opts.badge or "",
                disabled=opts.disabled,
                content=content,
            )
        )
    return items


@dataclass(frozen=True, slots=True)
class TabItemHandler:
    names: ClassVar[tuple[str, ...]] = ("tab-item",)
    token_type: ClassVar[str] = "tab_item"
    contract: ClassVar[DirectiveContract | None] = TAB_ITEM_CONTRACT
    options_class: ClassVar[type[TabItemOptions]] = TabItemOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: TabItemOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title or "Tab",
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        sb.append(_TAB_BOUNDARY)
        sb.append(rendered_children)
        sb.append(_TAB_BOUNDARY)


@dataclass(frozen=True, slots=True)
class TabSetHandler:
    names: ClassVar[tuple[str, ...]] = ("tab-set",)
    token_type: ClassVar[str] = "tab_set"
    contract: ClassVar[DirectiveContract | None] = TAB_SET_CONTRACT
    options_class: ClassVar[type[TabSetOptions]] = TabSetOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: TabSetOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        items = _tab_items_from_ast(node, rendered_children)
        if not items:
            sb.append(rendered_children)
            return
        opts = node.options
        digest = hashlib.sha256(rendered_children.encode()).hexdigest()[:8]
        sync_slug = _slug(opts.sync) if opts.sync else ""
        tabs: list[tuple[str, str, str, bool]] = []
        panels: list[str] = []
        icons: list[str] = []
        for index, item in enumerate(items):
            if opts.sync:
                tab_id = f"tab-{index}-{sync_slug}"
            else:
                tab_id = f"tab-{index}-{_slug(item.title)}-{digest}"
            tabs.append(
                (
                    tab_id,
                    as_markup(render_inline_text(item.title)),
                    item.badge,
                    item.selected,
                )
            )
            panels.append(item.content)
            icons.append(item.icon)
        sb.append(
            render_doc_tabs(
                tabs,
                panels,
                sync_key=opts.sync or "",
                variant="tabs",
                icons=icons,
            )
        )
