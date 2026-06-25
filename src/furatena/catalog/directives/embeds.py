"""Media embed directives — youtube, gist, figure."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions, StyledOptions
from patitas.nodes import Directive

from furatena.catalog.context import get_render_context
from furatena.catalog.directives.html import rewrite_href
from furatena.catalog.directives.kida_render import render_directive

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

GIST_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+/[a-f0-9]{32}$")
YOUTUBE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{11}$")


@dataclass(frozen=True, slots=True)
class YouTubeOptions(StyledOptions):
    title: str = ""
    aspect: str = "16/9"
    start: int = 0
    privacy: bool = True


@dataclass(frozen=True, slots=True)
class GistOptions(DirectiveOptions):
    file: str = ""


@dataclass(frozen=True, slots=True)
class FigureOptions(StyledOptions):
    _aliases: ClassVar[dict[str, str]] = {"class": "css_class"}

    alt: str = ""
    caption: str = ""
    width: str = ""
    loading: str = "lazy"
    css_class: str = ""


@dataclass(frozen=True, slots=True)
class YouTubeHandler:
    names: ClassVar[tuple[str, ...]] = ("youtube",)
    token_type: ClassVar[str] = "youtube"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[YouTubeOptions]] = YouTubeOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: YouTubeOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        video_id = title.strip() if title else ""
        return Directive(
            location=location,
            name=name,
            title=title,
            options=replace(options, title=options.title or f"YouTube video {video_id}"),
            children=(),
            raw_content=video_id,
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        video_id = (node.raw_content or "").strip()
        opts = node.options
        if not YOUTUBE_ID_PATTERN.match(video_id):
            sb.append(_error(f"Invalid YouTube video id: {video_id!r}"))
            return
        host = "www.youtube-nocookie.com" if opts.privacy else "www.youtube.com"
        embed_url = f"https://{host}/embed/{video_id}"
        if opts.start > 0:
            embed_url += f"?start={opts.start}"
        sb.append(
            render_directive(
                "youtube",
                embed_url=embed_url,
                title=opts.title or f"YouTube video {video_id}",
                aspect=opts.aspect,
                extra_class=opts.class_ or "",
            )
        )


@dataclass(frozen=True, slots=True)
class GistHandler:
    names: ClassVar[tuple[str, ...]] = ("gist",)
    token_type: ClassVar[str] = "gist"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[GistOptions]] = GistOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: GistOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=(),
            raw_content=title.strip() if title else "",
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        gist_ref = (node.raw_content or "").strip()
        if not GIST_ID_PATTERN.match(gist_ref):
            sb.append(_error(f"Invalid gist reference: {gist_ref!r}"))
            return
        file_q = node.options.file
        script_url = f"https://gist.github.com/{gist_ref}.js"
        if file_q:
            script_url += f"?file={escape(file_q, quote=True)}"
        gist_url = f"https://gist.github.com/{gist_ref}"
        sb.append(
            render_directive(
                "gist",
                script_url=script_url,
                gist_url=gist_url,
            )
        )


@dataclass(frozen=True, slots=True)
class FigureHandler:
    names: ClassVar[tuple[str, ...]] = ("figure",)
    token_type: ClassVar[str] = "figure"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[FigureOptions]] = FigureOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: FigureOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=(),
            raw_content=title.strip() if title else "",
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        src = rewrite_href((node.raw_content or "").strip())
        opts = node.options
        if not src:
            sb.append(_error("Figure requires an image path"))
            return
        sb.append(
            render_directive(
                "figure",
                src=src,
                alt=opts.alt or "",
                caption=opts.caption or "",
                width=opts.width or "",
                loading=opts.loading or "lazy",
                extra_class=opts.css_class or opts.class_ or "",
            )
        )


def _error(message: str) -> str:
    return (
        f'<aside class="chirpui-callout chirpui-callout--error">'
        f'<div class="chirpui-callout__body">{escape(message)}</div>'
        f"</aside>"
    )
