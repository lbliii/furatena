"""Edition lifecycle derivation, lookup, and selection contracts."""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from furatena.catalog.edition_lifecycle import (
    EDITION_RANK_MULTIPLIERS,
    EditionLifecycle,
    lifecycle_lookup,
    lifecycle_statuses,
)
from furatena.catalog.registry import CatalogRegistry
from furatena.catalog.sources.types import GitEditionPolicy, GitEditionSnapshot


def _snapshot(edition: str, status: str) -> GitEditionSnapshot:
    return GitEditionSnapshot(
        id=edition,
        ref=f"v{edition}" if edition != "latest" else "main",
        resolved_ref="a" * 40,
        content_root=Path("/tmp") / edition,
        status=status,
        prerelease=status == "preview",
        discovered_at="2026-08-03T00:00:00Z",
        release_date="2026-01-02" if edition != "latest" else None,
        end_of_life="2027-01-02" if status == "eol" else None,
        banner="Archived reference" if status == "eol" else None,
    )


def test_lifecycle_lookup_is_immutable_constant_time_registry_state() -> None:
    snapshots = (_snapshot("latest", "current"), _snapshot("1.0.0", "eol"))
    lookup = lifecycle_lookup(
        (SimpleNamespace(id="docs"),),
        {"docs": snapshots},
    )
    assert isinstance(lookup, MappingProxyType)
    registry = object.__new__(CatalogRegistry)
    registry._edition_lifecycle = lookup
    registry._edition_context = ContextVar("test_edition", default="latest")

    for _ in range(10_000):
        assert registry.edition_status_for("docs", "1.0.0") == "eol"
    with pytest.raises(KeyError, match="Unknown edition lifecycle namespace"):
        registry.edition_status_for("docs", "9.9.9")
    with pytest.raises(TypeError):
        lookup[("docs", "1.0.0")] = EditionLifecycle("docs", "1.0.0", "legacy")


def test_exact_status_is_opt_in_while_include_flags_broaden_defaults() -> None:
    assert lifecycle_statuses() == {"current", "legacy", "deprecated"}
    assert lifecycle_statuses(status="eol") == {"eol"}
    assert lifecycle_statuses(status="preview") == {"preview"}
    assert lifecycle_statuses(include_eol=True) == {
        "current",
        "legacy",
        "deprecated",
        "eol",
    }
    assert lifecycle_statuses(include_preview=True) == {
        "current",
        "legacy",
        "deprecated",
        "preview",
    }
    with pytest.raises(ValueError, match="status must be one of"):
        lifecycle_statuses(status="unsupported")
    assert EDITION_RANK_MULTIPLIERS["legacy"] < EDITION_RANK_MULTIPLIERS["current"]
    assert EDITION_RANK_MULTIPLIERS["deprecated"] < EDITION_RANK_MULTIPLIERS["legacy"]


def test_snapshot_lifecycle_metadata_round_trips_deterministically() -> None:
    snapshot = _snapshot("1.0.0", "eol")
    restored = GitEditionSnapshot.from_mapping(snapshot.to_dict())
    assert restored.to_dict() == {
        **snapshot.to_dict(),
        "content_root": str(snapshot.content_root.resolve()),
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {
                "1.0.0": {
                    "status": "legacy",
                    "release_date": "2027-01-02",
                    "end_of_life": "2027-01-01",
                }
            },
            "end_of_life must not precede release_date",
        ),
        ({"1.0.0": {"status": "eol"}}, "alias 'stable' cannot target 'eol'"),
    ],
)
def test_malformed_or_unsafe_lifecycle_config_is_actionable(
    overrides: dict[str, dict[str, str]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GitEditionPolicy.from_mount_dict(
            {
                "editions": {
                    "count": 1,
                    "aliases": {"stable": "1.0.0"},
                    "overrides": overrides,
                }
            },
            mount_id="docs",
            git_backed=True,
        )
