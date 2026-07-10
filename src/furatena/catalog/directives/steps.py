"""Steps directives — Kida templates with chirp-theme step visual skin."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, ClassVar

from patitas.directives.contracts import DirectiveContract
from patitas.directives.options import StyledOptions
from patitas.nodes import Directive

from furatena.catalog.directives.html import render_inline_text
from furatena.catalog.directives.kida_render import render_directive, trusted_renderer_html

if TYPE_CHECKING:
    from collections.abc import Sequence

    from patitas.location import SourceLocation
    from patitas.nodes import Block
    from patitas.stringbuilder import StringBuilder

STEPS_CONTRACT = DirectiveContract(requires_children=("step",), allows_children=("step",))
STEP_CONTRACT = DirectiveContract(requires_parent=("steps",))


def _slugify_step_id(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "step"


@dataclass(frozen=True, slots=True)
class StepsOptions(StyledOptions):
    start: int = 1
    style: str | None = None


@dataclass(frozen=True, slots=True)
class StepOptions(StyledOptions):
    description: str | None = None
    optional: bool = False
    duration: str | None = None
    step_number: int | None = None
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class StepsHandler:
    names: ClassVar[tuple[str, ...]] = ("steps",)
    token_type: ClassVar[str] = "steps"
    contract: ClassVar[DirectiveContract | None] = STEPS_CONTRACT
    options_class: ClassVar[type[StepsOptions]] = StepsOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: StepsOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        start = options.start
        processed: list[Block] = []
        step_num = start
        for child in children:
            if isinstance(child, Directive) and child.name == "step":
                child_opts = child.options
                new_opts = replace(
                    child_opts,
                    step_number=step_num,
                    heading_level=2,
                )
                step_num += 1
                processed.append(
                    Directive(
                        location=child.location,
                        name=child.name,
                        title=child.title,
                        options=new_opts,
                        children=child.children,
                    )
                )
            else:
                processed.append(child)
        return Directive(
            location=location,
            name=name,
            title=title,
            options=options,
            children=tuple(processed),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        opts = node.options
        sb.append(
            render_directive(
                "steps",
                body=trusted_renderer_html(rendered_children),
                start=opts.start,
                style=opts.style or "default",
                extra_class=opts.class_ or "",
            )
        )


@dataclass(frozen=True, slots=True)
class StepHandler:
    names: ClassVar[tuple[str, ...]] = ("step",)
    token_type: ClassVar[str] = "step"
    contract: ClassVar[DirectiveContract | None] = STEP_CONTRACT
    options_class: ClassVar[type[StepOptions]] = StepOptions
    preserves_raw_content: ClassVar[bool] = False

    def parse(
        self,
        name: str,
        title: str | None,
        options: StepOptions,
        content: str,
        children: Sequence[Block],
        location: SourceLocation,
    ) -> Directive[Any]:
        return Directive(
            location=location,
            name=name,
            title=title or "Step",
            options=options,
            children=tuple(children),
        )

    def render(self, node: Directive[Any], rendered_children: str, sb: StringBuilder) -> None:
        opts = node.options
        title = node.title or ""
        step_number = opts.step_number if opts.step_number is not None else 1
        heading_level = opts.heading_level if opts.heading_level is not None else 2
        step_id = _slugify_step_id(title) if title else f"step-{step_number}"
        title_html = render_inline_text(title) if title else ""
        description_html = render_inline_text(opts.description) if opts.description else ""
        sb.append(
            render_directive(
                "step",
                title=title_html,
                body=trusted_renderer_html(rendered_children),
                description=description_html,
                duration=opts.duration or "",
                optional=opts.optional,
                step_number=step_number,
                heading_level=heading_level,
                step_id=step_id,
                extra_class=opts.class_ or "",
            )
        )
