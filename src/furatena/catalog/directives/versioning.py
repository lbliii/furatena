"""Version badges and related links via Kida."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions, StyledOptions
from patitas.nodes import Directive

from furatena.catalog.context import get_render_context
from furatena.catalog.directives.html import render_inline_text
from furatena.catalog.directives.kida_render import as_markup, render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder


@dataclass(frozen=True, slots=True)
class VersionOptions(StyledOptions):
    version: str = ""


@dataclass(frozen=True, slots=True)
class SinceHandler:
    names: ClassVar[tuple[str, ...]] = ("since",)
    token_type: ClassVar[str] = "since"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[VersionOptions]] = VersionOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: VersionOptions,
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
        version = node.options.version or node.title or ""
        sb.append(
            render_directive(
                "version_callout",
                kind="since",
                badge_label=f"Since {version}".strip(),
                body=rendered_children,
            )
        )


@dataclass(frozen=True, slots=True)
class DeprecatedHandler:
    names: ClassVar[tuple[str, ...]] = ("deprecated", "changed")
    token_type: ClassVar[str] = "deprecated"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[VersionOptions]] = VersionOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: VersionOptions,
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
        version = node.options.version or node.title or ""
        kind = "changed" if node.name == "changed" else "deprecated"
        sb.append(
            render_directive(
                "version_callout",
                kind=kind,
                badge_label=f"{node.name.capitalize()} {version}".strip(),
                body=rendered_children,
            )
        )


@dataclass(frozen=True, slots=True)
class RelatedOptions(DirectiveOptions):
    _aliases: ClassVar[dict[str, str]] = {"section-title": "section_title"}

    limit: int = 0
    section_title: str = ""


@dataclass(frozen=True, slots=True)
class RelatedHandler:
    names: ClassVar[tuple[str, ...]] = ("related",)
    token_type: ClassVar[str] = "related"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[RelatedOptions]] = RelatedOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: RelatedOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title or "Related",
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        body = rendered_children.strip()
        opts = node.options
        links: list[dict[str, str]] = []
        if not body:
            ctx = get_render_context()
            if ctx is not None:
                stub = ctx.stubs.get(ctx.current_slug)
                if stub:
                    links = list(ctx.get_backlinks(stub.url))
                    if opts.limit > 0:
                        links = links[: opts.limit]

        title = as_markup(render_inline_text(opts.section_title or node.title or "Related"))
        sb.append(
            render_directive(
                "related",
                title=title,
                body=body,
                links=links,
            )
        )
