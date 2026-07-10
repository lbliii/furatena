"""Code-tabs directive — fenced code blocks as Alpine + chirp-ui tabs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.options import DirectiveOptions
from patitas.nodes import Directive

from furatena.catalog.directives.kida_render import render_doc_tabs
from furatena.catalog.highlight import highlight_code_block

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

_CODE_BLOCK_RE = re.compile(
    r"```(\w+)?(?:[ \t]+([^\n]*))?\n(.*?)```",
    re.DOTALL,
)
_FENCE_TITLE_RE = re.compile(r"""title=(["'])(.*?)\1""", re.IGNORECASE)


def _sync_slug(key: str) -> str:
    slug = re.sub(r"[^\w-]", "-", key.lower()).strip("-")
    return slug or "sync"


def _tab_id(*, index: int, sync_key: str, lang: str, digest: str) -> str:
    if sync_key and sync_key.lower() != "none":
        return f"code-{index}-{_sync_slug(sync_key)}"
    return f"code-{index}-{lang}-{digest}"


_LANG_LABELS = {
    "py": "Python",
    "python": "Python",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "bash": "Shell",
    "sh": "Shell",
    "html": "HTML",
}


@dataclass(frozen=True, slots=True)
class CodeTabsOptions(DirectiveOptions):
    sync: str | None = None


def _label_for(lang: str, info: str) -> str:
    if info.strip():
        title_match = _FENCE_TITLE_RE.search(info)
        if title_match:
            return title_match.group(2).strip()
        first = info.strip().split()[0]
        if first and not first.startswith("{"):
            return first.rsplit(".", 1)[0].replace("-", " ").title()
    return _LANG_LABELS.get(lang.lower(), lang.capitalize() if lang else "Code")


@dataclass(frozen=True, slots=True)
class CodeTabsHandler:
    names: ClassVar[tuple[str, ...]] = ("code-tabs",)
    token_type: ClassVar[str] = "code_tabs"
    contract: ClassVar[None] = None
    options_class: ClassVar[type[CodeTabsOptions]] = CodeTabsOptions
    preserves_raw_content: ClassVar[bool] = True

    def parse(
        self,
        name: str,
        title: str | None,
        options: CodeTabsOptions,
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
            raw_content=content,
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        del rendered_children
        raw = node.raw_content or ""
        blocks = list(_CODE_BLOCK_RE.finditer(raw))
        if not blocks:
            sb.append(f"<pre>{escape(raw)}</pre>")
            return
        digest = hashlib.sha256(raw.encode()).hexdigest()[:8]
        sync_key = node.options.sync or "language"
        tabs: list[tuple[str, str, str, bool]] = []
        panels: list[str] = []
        for index, match in enumerate(blocks):
            lang = match.group(1) or "text"
            info = match.group(2) or ""
            code = match.group(3).rstrip("\n")
            label = _label_for(lang, info)
            tab_id = _tab_id(index=index, sync_key=sync_key, lang=lang, digest=digest)
            selected = index == 0
            tabs.append((tab_id, label, "", selected))
            panels.append(highlight_code_block(lang, code))
        sb.append(
            render_doc_tabs(
                tabs,
                panels,
                sync_key=sync_key if sync_key.lower() != "none" else "",
                variant="code-tabs",
            )
        )
