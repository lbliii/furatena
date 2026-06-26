"""Render-time context for Patitas directive handlers."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


@dataclass
class NodeStub:
    """Lightweight page record available before full HTML render."""

    slug: str
    url: str
    title: str
    description: str
    weight: int
    page_type: str = "page"


@dataclass
class RenderContext:
    """Context passed to directive handlers during markdown render."""

    content_root: Path
    source_rel: str
    current_slug: str
    stubs: dict[str, NodeStub]
    render_markdown: Callable[[str, str, str, set[str], int], str]
    get_backlinks: Callable[[str], list[dict[str, str]]]
    include_stack: set[str] = field(default_factory=set)
    include_depth: int = 0


_render_ctx: ContextVar[RenderContext | None] = ContextVar("chirp_docs_render_ctx", default=None)


def set_render_context(ctx: RenderContext) -> ContextVar.Token:
    return _render_ctx.set(ctx)


def reset_render_context(token: ContextVar.Token) -> None:
    _render_ctx.reset(token)


def get_render_context() -> RenderContext | None:
    return _render_ctx.get()


def get_stub(slug: str) -> NodeStub | None:
    ctx = get_render_context()
    if ctx is None:
        return None
    return ctx.stubs.get(slug.strip("/"))


def child_stubs(parent_slug: str) -> list[NodeStub]:
    ctx = get_render_context()
    if ctx is None:
        return []
    parent_parts = parent_slug.strip("/").split("/")
    depth = len(parent_parts)
    prefix = "/".join(parent_parts)
    children: list[NodeStub] = []
    for stub in ctx.stubs.values():
        parts = stub.slug.split("/")
        if len(parts) != depth + 1:
            continue
        if "/".join(parts[:-1]) != prefix:
            continue
        if stub.slug == parent_slug:
            continue
        children.append(stub)
    return sorted(children, key=lambda s: (s.weight, s.title))
