"""Kida presentation IR checks for Furatena views."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kida.analysis import check_context_contract

from furatena.catalog.template_env import (
    build_docs_template_env,
    check_search_shell_templates,
    registered_template_globals,
)
from furatena.catalog.view_kinds import VIEW_KINDS, ViewKindSpec

if TYPE_CHECKING:
    from pathlib import Path

    from kida import Environment

    from furatena.catalog.config import DocsConfig
    from furatena.catalog.registry import CatalogRegistry
    from furatena.catalog.theme import DocsTheme


def _registered_view_templates(docs: DocsConfig) -> dict[str, ViewKindSpec | None]:
    templates: dict[str, ViewKindSpec | None] = {}
    for spec in VIEW_KINDS:
        templates[spec.default_template] = spec
    for template in docs.views.values():
        if template not in templates:
            templates[template] = None
    for template in docs.overrides.values():
        if template not in templates:
            templates[template] = None
    return templates


def check_view_templates(
    docs: DocsConfig,
    theme: DocsTheme,
    *,
    strict: bool = False,
    repo_root: Path | None = None,
    catalog: CatalogRegistry | None = None,
    env: Environment | None = None,
) -> tuple[list[str], list[str]]:
    """Validate view templates declare required blocks and context."""
    errors: list[str] = []
    warnings: list[str] = []
    env = env or build_docs_template_env(docs, theme, repo_root=repo_root)
    template_globals = registered_template_globals(env)

    for template_name, kind_spec in sorted(_registered_view_templates(docs).items()):
        try:
            template = env.get_template(template_name)
        except Exception as exc:
            errors.append(f"{template_name}: failed to load ({exc})")
            continue

        blocks = set(template.block_metadata())
        required_blocks = kind_spec.required_blocks if kind_spec else frozenset({"page_root"})
        missing_blocks = required_blocks - blocks
        if missing_blocks:
            errors.append(
                f"{template_name}: missing required blocks: {', '.join(sorted(missing_blocks))}",
            )

        if kind_spec is None:
            continue

        issues = check_context_contract(
            template,
            kind_spec.required_context,
            optional=kind_spec.optional_context,
            globals=template_globals,
        )
        for issue in issues:
            message = f"{template_name}: {issue.message}"
            if issue.code == "K-CTX-002":
                errors.append(message)
            elif issue.code == "K-CTX-001":
                if strict and kind_spec.surface == "catalog":
                    errors.append(message)
                else:
                    warnings.append(message)

    if catalog is not None and repo_root is not None:
        errors.extend(check_search_shell_templates(env, catalog))

    return sorted(errors), sorted(warnings)
