"""Patitas role registry for Furatena."""

from __future__ import annotations

from patitas.roles.registry import RoleRegistryBuilder

from furatena.catalog.roles.glossary_term import GtermRole
from furatena.catalog.roles.inventory import PyRole
from furatena.catalog.roles.xref import XrefRole


def create_role_registry():
    """Return inline role handlers for documentation content."""
    builder = RoleRegistryBuilder()
    builder.register(GtermRole())
    builder.register(XrefRole())
    builder.register(PyRole())
    return builder.build()
