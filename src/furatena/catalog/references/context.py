"""Reference resolution context for Patitas roles."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from furatena.catalog.inventories.store import InventoryStore
    from furatena.catalog.registry import CatalogRegistry


@dataclass(frozen=True, slots=True)
class ReferenceContext:
    catalog: CatalogRegistry | None = None
    inventory_store: InventoryStore | None = None


_ref_ctx: ContextVar[ReferenceContext | None] = ContextVar("chirp_docs_ref_ctx", default=None)


def set_reference_context(ctx: ReferenceContext | None) -> ContextVar.Token:
    return _ref_ctx.set(ctx)


def reset_reference_context(token: ContextVar.Token) -> None:
    _ref_ctx.reset(token)


def get_reference_context() -> ReferenceContext | None:
    return _ref_ctx.get()
