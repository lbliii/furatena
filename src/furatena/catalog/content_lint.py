"""Markdown content lint rules for Furatena check."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from patitas.directives.contracts import ContractViolation
from patitas.linting.diagnostic import Severity
from patitas.linting.runner import lint as patitas_lint
from patitas.nodes import Block, Directive, Document

if TYPE_CHECKING:
    from furatena.catalog.models import ContentIR


class DirectiveRegistryLike(Protocol):
    def get(self, name: str) -> object | None: ...

    @property
    def names(self) -> frozenset[str]: ...


def lint_patitas_document(
    document: Document,
    *,
    source: str,
    body: str = "",
) -> tuple[list[str], list[str]]:
    """Run Patitas' built-in markdown lint rules on a parsed document."""
    errors: list[str] = []
    warnings: list[str] = []
    diagnostics = patitas_lint(document, text=body, source_file=source)
    for diagnostic in diagnostics:
        line = diagnostic.location.lineno if diagnostic.location is not None else None
        message = _message(source, line, diagnostic.message)
        if diagnostic.severity == Severity.ERROR:
            errors.append(message)
        else:
            warnings.append(message)
    return sorted(errors), sorted(warnings)


def lint_directive_contracts(
    document: Document,
    registry: DirectiveRegistryLike,
    *,
    source: str,
) -> tuple[list[str], list[str]]:
    """Validate directive nesting against handler contracts."""
    errors: list[str] = []
    warnings: list[str] = []

    def record(violation: ContractViolation, line: int | None) -> None:
        message = _message(source, line, violation.message)
        if violation.violation_type in {
            "missing_parent",
            "wrong_parent",
            "missing_required_child",
            "forbidden_child",
            "too_many_children",
        }:
            errors.append(message)
        else:
            warnings.append(message)

    def walk(blocks: tuple[Block, ...] | list[Block], parent_name: str | None) -> None:
        for block in blocks:
            if isinstance(block, Directive):
                handler = registry.get(block.name)
                contract = getattr(handler, "contract", None) if handler is not None else None
                line = _node_line(block)
                if contract is not None:
                    parent_violation = contract.validate_parent(block.name, parent_name)
                    if parent_violation is not None:
                        record(parent_violation, line)
                    child_directives = _directive_children(block)
                    for child_violation in contract.validate_children(block.name, child_directives):
                        record(child_violation, line)
                walk(block.children, block.name)
            elif getattr(block, "children", None):
                walk(block.children, parent_name)

    walk(document.children, None)
    return sorted(errors), sorted(warnings)


def lint_unknown_directives(
    content_ir: ContentIR,
    registry: DirectiveRegistryLike,
    *,
    source: str,
) -> list[str]:
    """Warn when markdown uses directive names not registered for this app."""
    warnings: list[str] = []
    known = registry.names
    for directive in content_ir.directives:
        if directive.name and directive.name not in known:
            warnings.append(
                _message(source, directive.line, f"unknown directive: {directive.name}"),
            )
    return warnings


def lint_page_ast(
    document: Document,
    content_ir: ContentIR,
    registry: DirectiveRegistryLike,
    *,
    source: str,
    body: str = "",
) -> tuple[list[str], list[str]]:
    """Run Patitas lint plus Furatena directive contract checks."""
    errors: list[str] = []
    warnings: list[str] = []

    patitas_errors, patitas_warnings = lint_patitas_document(
        document,
        source=source,
        body=body,
    )
    errors.extend(patitas_errors)
    warnings.extend(patitas_warnings)

    contract_errors, contract_warnings = lint_directive_contracts(
        document,
        registry,
        source=source,
    )
    errors.extend(contract_errors)
    warnings.extend(contract_warnings)

    warnings.extend(lint_unknown_directives(content_ir, registry, source=source))
    return sorted(errors), sorted(warnings)


def _directive_children(node: Directive) -> list[Directive]:
    return [child for child in node.children if isinstance(child, Directive)]


def _node_line(node: Block) -> int | None:
    location = getattr(node, "location", None)
    if location is None:
        return None
    return getattr(location, "lineno", None)


def _message(source: str, line: int | None, text: str) -> str:
    if line is None:
        return f"{source}: {text}"
    return f"{source}:{line}: {text}"
