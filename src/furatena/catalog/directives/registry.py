"""Build the Patitas directive registry for Furatena."""

from __future__ import annotations

from patitas import DirectiveRegistryBuilder
from patitas.directives.registry import DirectiveRegistry

from furatena.catalog.directives.admonition import AdmonitionHandler
from furatena.catalog.directives.cards import CardHandler, CardsHandler
from furatena.catalog.directives.child_cards import ChildCardsHandler
from furatena.catalog.directives.code_tabs import CodeTabsHandler
from furatena.catalog.directives.dropdown import DropdownHandler
from furatena.catalog.directives.embeds import FigureHandler, GistHandler, YouTubeHandler
from furatena.catalog.directives.glossary import GlossaryHandler
from furatena.catalog.directives.include import IncludeHandler
from furatena.catalog.directives.list_table import ListTableHandler
from furatena.catalog.directives.literalinclude import LiteralIncludeHandler
from furatena.catalog.directives.steps import StepHandler, StepsHandler
from furatena.catalog.directives.tabs import TabItemHandler, TabSetHandler
from furatena.catalog.directives.versioning import DeprecatedHandler, RelatedHandler, SinceHandler


def create_directive_registry() -> DirectiveRegistry:
    """Return a registry of chirp-ui native directive handlers."""
    builder = DirectiveRegistryBuilder()
    builder.register(AdmonitionHandler())
    builder.register(CardsHandler())
    builder.register(CardHandler())
    builder.register(ChildCardsHandler())
    builder.register(GlossaryHandler())
    builder.register(YouTubeHandler())
    builder.register(GistHandler())
    builder.register(FigureHandler())
    builder.register(DropdownHandler())
    builder.register(TabSetHandler())
    builder.register(TabItemHandler())
    builder.register(CodeTabsHandler())
    builder.register(StepsHandler())
    builder.register(StepHandler())
    builder.register(SinceHandler())
    builder.register(DeprecatedHandler())
    builder.register(RelatedHandler())
    builder.register(ListTableHandler())
    builder.register(IncludeHandler())
    builder.register(LiteralIncludeHandler())
    return builder.build()
