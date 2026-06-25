"""Documentation catalog — docs as data."""

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.loader import DocCatalog
from furatena.catalog.models import DocNode, TocEntry
from furatena.catalog.registry import CatalogRegistry, MountConfig, load_mounts
from furatena.catalog.views import ViewRegistry
from furatena.catalog.view_kinds import VIEW_KINDS, ViewKindSpec

__all__ = [
    "CatalogRegistry",
    "DocCatalog",
    "DocNode",
    "DocsApp",
    "MountConfig",
    "TocEntry",
    "ViewKindSpec",
    "ViewRegistry",
    "VIEW_KINDS",
    "load_mounts",
]
