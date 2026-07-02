"""MyST-flavored markdown adapter."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from furatena.catalog.models import ContentIR
from furatena.catalog.render import DocsRenderer
from furatena.catalog.sources.adapters.markdown import PatitasMarkdownAdapter
from furatena.catalog.sources.types import AdaptedContent, PageSource

_MYST_FENCE_DIRECTIVE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})\{(?P<name>[A-Za-z][\w-]*)\}(?P<argument>[^\n]*)\n"
    r"(?P<body>.*?)"
    r"^(?P=fence)\s*$",
    re.DOTALL | re.MULTILINE,
)
_MYST_ROLE = re.compile(r"\{(?P<name>[A-Za-z][\w-]*)\}`(?P<target>[^`]+)`")


def myst_to_markdown(source: str) -> str:
    """Lower supported MyST constructs into Patitas markdown."""
    converted = _MYST_FENCE_DIRECTIVE.sub(_replace_directive, source)
    return _MYST_ROLE.sub(_replace_role, converted)


def _replace_directive(match: re.Match[str]) -> str:
    name = match.group("name")
    argument = match.group("argument").strip()
    body = match.group("body").strip("\n")
    opening = f":::{{{name}}}"
    if argument:
        opening = f"{opening} {argument}"
    return f"{opening}\n{body}\n:::"


def _replace_role(match: re.Match[str]) -> str:
    name = match.group("name").lower()
    label, target = _split_role_target(match.group("target").strip())
    if name == "ref":
        href = target if target.startswith(("#", "/", "http://", "https://")) else f"#{target}"
        return f"[{label}]({href})"
    if name == "doc":
        href = target if target.startswith(("/", "http://", "https://")) else f"/{target.strip('/')}/"
        return f"[{label}]({href})"
    return match.group(0)


def _split_role_target(raw: str) -> tuple[str, str]:
    match = re.fullmatch(r"(?P<label>.+?)\s*<(?P<target>[^>]+)>", raw)
    if match:
        return match.group("label").strip(), match.group("target").strip()
    return raw, raw


class MystMarkdownAdapter:
    """Adapt MyST markdown by lowering known MyST constructs to Patitas markdown."""

    content_format = "myst-markdown"

    def __init__(self, renderer: DocsRenderer | None = None) -> None:
        self._markdown = PatitasMarkdownAdapter(renderer=renderer)

    def parse(self, body: str) -> tuple[object | None, ContentIR | None]:
        return self._markdown.parse(myst_to_markdown(body))

    def parse_incremental(
        self,
        body: str,
        previous: object | None,
        *,
        previous_body: str = "",
    ) -> tuple[object | None, ContentIR | None]:
        return self._markdown.parse_incremental(
            myst_to_markdown(body),
            previous,
            previous_body=myst_to_markdown(previous_body) if previous_body else "",
        )

    def invalidation_regions(self, old: object | None, new: object | None) -> set[str]:
        return set(self._markdown.invalidation_regions(old, new))

    def invalidation_hints(self, old: object | None, new: object | None) -> tuple[str, ...]:
        return self._markdown.invalidation_hints(old, new)

    def adapt(
        self,
        source: PageSource,
        *,
        stubs,
        render_markdown: Callable[..., str],
        get_backlinks: Callable[[str], list[dict[str, str]]],
        content_root: Path,
        include_stack: set[str] | None = None,
        include_depth: int = 0,
        document: object | None = None,
        mount: str | None = None,
    ) -> AdaptedContent:
        lowered = myst_to_markdown(source.body)
        lowered_source = PageSource(
            path=source.path,
            content_format="patitas-markdown",
            meta=source.meta,
            body=lowered,
            source_path=source.source_path,
            url=source.url,
            slug=source.slug,
        )
        return self._markdown.adapt(
            lowered_source,
            stubs=stubs,
            render_markdown=render_markdown,
            get_backlinks=get_backlinks,
            content_root=content_root,
            include_stack=include_stack,
            include_depth=include_depth,
            document=document,
            mount=mount,
        )
