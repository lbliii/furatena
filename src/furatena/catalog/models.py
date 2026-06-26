"""Doc catalog data models."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TocEntry:
    """One heading in a document table of contents."""

    anchor: str
    text: str
    depth: int


@dataclass(frozen=True, slots=True)
class ContentHeading:
    """One heading extracted from Patitas content IR."""

    level: int
    text: str
    anchor: str
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ContentLink:
    """One link extracted from Patitas content IR or reference roles."""

    href: str
    text: str
    line: int | None = None
    mount: str | None = None
    domain: str | None = None
    inventory_id: str | None = None
    resolved: bool = True


@dataclass(frozen=True, slots=True)
class ContentDirective:
    """One directive block extracted from Patitas content IR."""

    name: str
    options: dict[str, Any]
    line: int | None = None


@dataclass(frozen=True, slots=True)
class ContentIR:
    """Normalized structured content extracted from any source adapter."""

    headings: tuple[ContentHeading, ...] = ()
    links: tuple[ContentLink, ...] = ()
    directives: tuple[ContentDirective, ...] = ()
    features: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SectionChunk:
    """One heading-bounded section of page text for search and export."""

    id: str
    heading: str
    depth: int
    text: str


@dataclass(frozen=True, slots=True)
class DocNode:
    """One indexed documentation page."""

    url: str
    slug: str
    title: str
    description: str
    layout: str
    weight: int
    section: str
    tags: frozenset[str]
    body_md: str
    body_html: str
    toc: tuple[TocEntry, ...]
    source_path: str
    meta: dict[str, Any] = field(default_factory=dict)
    mount: str = "chirp"
    edition: str = "latest"
    lang: str = "en"
    translation_key: str | None = None
    section_root: bool = False
    html_path: str | None = None
    content_ir: ContentIR | None = None
    ast_json: str | None = None
    content_format: str = "patitas-markdown"
    body_text: str = ""
    sections: tuple[SectionChunk, ...] = ()

    @property
    def view_kind(self) -> str:
        """Author-facing page kind (front matter ``layout`` / ``kind``)."""
        return self.layout

    @property
    def body_source(self) -> str:
        """Authoring-format source body (alias for legacy ``body_md`` field)."""
        return self.body_md

    @property
    def node_id(self) -> str:
        from furatena.catalog.graph_schema import make_node_id

        return make_node_id(self.mount, self.edition, self.slug)
