"""Machine-readable directive registry for Furatena."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DOCS_TEMPLATES = Path(__file__).resolve().parents[1] / "_templates"
DIRECTIVES_CSS = Path(__file__).resolve().parents[2] / "theme" / "directives.css"


@dataclass(frozen=True, slots=True)
class DirectiveEntry:
    """One registered Patitas directive and its directive-skin contract."""

    names: tuple[str, ...]
    template: str | None
    theme_hook: str
    handler: str
    parent: tuple[str, ...] = ()
    children: tuple[str, ...] = ()


DIRECTIVE_MANIFEST: tuple[DirectiveEntry, ...] = (
    DirectiveEntry(
        names=("note", "tip", "warning", "danger", "error", "info", "important", "example", "success", "caution", "seealso"),
        template="callout",
        theme_hook="chirp-theme-directive-admonition",
        handler="AdmonitionHandler",
    ),
    DirectiveEntry(
        names=("cards",),
        template="card_grid",
        theme_hook="chirp-theme-directive-cards",
        handler="CardsHandler",
        children=("card",),
    ),
    DirectiveEntry(
        names=("card",),
        template="card_link",
        theme_hook="chirp-theme-directive-card",
        handler="CardHandler",
        parent=("cards",),
    ),
    DirectiveEntry(
        names=("child-cards",),
        template="child_cards",
        theme_hook="chirp-theme-directive-cards--children",
        handler="ChildCardsHandler",
    ),
    DirectiveEntry(
        names=("glossary",),
        template="glossary",
        theme_hook="chirp-theme-directive-glossary",
        handler="GlossaryHandler",
    ),
    DirectiveEntry(
        names=("youtube",),
        template="youtube",
        theme_hook="chirp-theme-directive-embed--youtube",
        handler="YouTubeHandler",
    ),
    DirectiveEntry(
        names=("gist",),
        template="gist",
        theme_hook="chirp-theme-directive-embed--gist",
        handler="GistHandler",
    ),
    DirectiveEntry(
        names=("figure",),
        template="figure",
        theme_hook="chirp-theme-directive-figure",
        handler="FigureHandler",
    ),
    DirectiveEntry(
        names=("dropdown",),
        template="accordion",
        theme_hook="chirp-theme-directive-dropdown",
        handler="DropdownHandler",
    ),
    DirectiveEntry(
        names=("tab-set",),
        template="tabs",
        theme_hook="chirp-theme-directive-tabs",
        handler="TabSetHandler",
        children=("tab-item",),
    ),
    DirectiveEntry(
        names=("tab-item",),
        template=None,
        theme_hook="chirp-theme-directive-tabs",
        handler="TabItemHandler",
        parent=("tab-set",),
    ),
    DirectiveEntry(
        names=("code-tabs",),
        template="tabs",
        theme_hook="chirp-theme-directive-tabs--code",
        handler="CodeTabsHandler",
    ),
    DirectiveEntry(
        names=("steps",),
        template="steps",
        theme_hook="chirp-theme-directive-steps",
        handler="StepsHandler",
        children=("step",),
    ),
    DirectiveEntry(
        names=("step",),
        template="step",
        theme_hook="chirp-theme-directive-steps__step",
        handler="StepHandler",
        parent=("steps",),
    ),
    DirectiveEntry(
        names=("since",),
        template="version_callout",
        theme_hook="chirp-theme-directive-version",
        handler="SinceHandler",
    ),
    DirectiveEntry(
        names=("deprecated", "changed"),
        template="version_callout",
        theme_hook="chirp-theme-directive-version",
        handler="DeprecatedHandler",
    ),
    DirectiveEntry(
        names=("related",),
        template="related",
        theme_hook="chirp-theme-directive-related",
        handler="RelatedHandler",
    ),
    DirectiveEntry(
        names=("list-table",),
        template="table",
        theme_hook="chirp-theme-directive-table",
        handler="ListTableHandler",
    ),
    DirectiveEntry(
        names=("include",),
        template=None,
        theme_hook="",
        handler="IncludeHandler",
    ),
    DirectiveEntry(
        names=("literalinclude",),
        template="literalinclude",
        theme_hook="chirp-theme-directive-figure--code",
        handler="LiteralIncludeHandler",
    ),
    DirectiveEntry(
        names=("code_block",),
        template="code_block",
        theme_hook="code-block-wrapper",
        handler="(fenced code)",
    ),
)


def check_manifest_registry_alignment() -> list[str]:
    """Return errors when manifest and Patitas registry diverge."""
    from furatena.catalog.directives.registry import create_directive_registry

    registry = create_directive_registry()
    registered_names = frozenset(registry.names)
    manifest_names = manifest_directive_names()
    errors: list[str] = []

    for name in sorted(manifest_names - registered_names):
        if name == "code_block":
            continue
        errors.append(f"manifest: directive {name!r} is not registered in create_directive_registry()")
    for name in sorted(registered_names - manifest_names):
        errors.append(f"manifest: registered directive {name!r} is missing from DIRECTIVE_MANIFEST")

    manifest_handlers = manifest_handler_names() - {"(fenced code)"}
    registry_handlers = frozenset(type(handler).__name__ for handler in registry.handlers)
    for handler in sorted(manifest_handlers - registry_handlers):
        errors.append(f"manifest: handler {handler!r} is not registered")
    for handler in sorted(registry_handlers - manifest_handlers):
        errors.append(f"manifest: registered handler {handler!r} is missing from DIRECTIVE_MANIFEST")

    return errors


def manifest_handler_names() -> frozenset[str]:
    return frozenset(entry.handler for entry in DIRECTIVE_MANIFEST)


def manifest_directive_names() -> frozenset[str]:
    names: set[str] = set()
    for entry in DIRECTIVE_MANIFEST:
        names.update(entry.names)
    return frozenset(names)


def validate_directive_manifest() -> tuple[list[str], list[str]]:
    """Verify manifest templates and theme hooks exist on disk."""
    errors: list[str] = []
    warnings: list[str] = []
    css_text = DIRECTIVES_CSS.read_text(encoding="utf-8") if DIRECTIVES_CSS.is_file() else ""

    for entry in DIRECTIVE_MANIFEST:
        if entry.template:
            template_path = DOCS_TEMPLATES / "directives" / f"{entry.template}.html"
            if not template_path.is_file():
                errors.append(f"manifest: missing template directives/{entry.template}.html for {entry.handler}")
        if entry.theme_hook and entry.theme_hook not in css_text:
            warnings.append(
                f"manifest: theme hook {entry.theme_hook!r} not found in theme/directives.css ({entry.handler})"
            )

    return sorted(errors), sorted(warnings)
