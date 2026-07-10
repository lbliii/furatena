"""Author mutation store contract and free-threading safety."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from furatena.catalog.access import AccessSubject
from furatena.catalog.author_store import (
    AuthorSourceConflict,
    AuthorSourceNotFound,
    FilesystemAuthorMutationStore,
    InMemoryAuthorMutationStore,
    source_revision,
)
from furatena.catalog.registry import load_mounts
from furatena.cli.authoring import (
    author_apply_edit,
    author_new,
    author_read_source,
    author_transition,
)
from furatena.cli.main import main


@pytest.fixture(params=("memory", "filesystem"))
def author_store(request: pytest.FixtureRequest, tmp_path: Path):
    if request.param == "memory":
        return InMemoryAuthorMutationStore(), tmp_path / "memory.md"
    return FilesystemAuthorMutationStore(), tmp_path / "filesystem.md"


def test_author_store_create_read_replace_contract(author_store) -> None:
    store, path = author_store
    initial = "# Initial\n"
    changed = "# Changed\n"

    assert store.exists(path) is False
    with pytest.raises(AuthorSourceNotFound):
        store.read(path)
    with pytest.raises(AuthorSourceNotFound):
        store.replace(path, changed, expected_revision=source_revision(initial))

    created = store.create(path, initial)
    assert created.path == path.resolve()
    assert created.source == initial
    assert created.revision == source_revision(initial)
    assert store.exists(path) is True
    assert store.read(path) == created

    with pytest.raises(AuthorSourceConflict) as duplicate:
        store.create(path, changed)
    assert duplicate.value.current_revision == created.revision

    with pytest.raises(AuthorSourceConflict) as missing_revision:
        store.replace(path, changed, expected_revision=None)
    assert missing_revision.value.current_revision == created.revision

    with pytest.raises(AuthorSourceConflict) as stale:
        store.replace(path, changed, expected_revision=source_revision("stale"))
    assert stale.value.current_revision == created.revision
    assert store.read(path) == created

    replaced = store.replace(path, changed, expected_revision=created.revision)
    assert replaced.source == changed
    assert replaced.revision == source_revision(changed)
    assert store.read(path) == replaced


def test_in_memory_author_store_does_not_claim_durability(tmp_path: Path) -> None:
    path = tmp_path / "virtual.md"
    first = InMemoryAuthorMutationStore()
    first.create(path, "# Process local\n")

    assert path.exists() is False
    assert first.exists(path) is True
    assert InMemoryAuthorMutationStore().exists(path) is False


def test_author_operations_use_injected_store_instead_of_raw_files(tmp_path: Path) -> None:
    app_root = tmp_path / "docs-site"
    main(["init", str(app_root), "--name", "Stored Authoring"])
    mounts = load_mounts(app_root / "mounts.yaml", repo_root=app_root)
    target = app_root / "content" / "docs" / "get-started.md"
    disk_source = target.read_text(encoding="utf-8")
    store = InMemoryAuthorMutationStore({target: disk_source})
    subject = AccessSubject.from_values(actor="store-test", roles=["admin"])

    read, source = author_read_source(
        "docs/get-started", mounts=mounts, subject=subject, store=store
    )
    assert read.ok and source == disk_source and read.source_revision

    edited = author_apply_edit(
        "docs/get-started",
        mounts=mounts,
        subject=subject,
        old_text="# Get started",
        new_text="# Stored edit",
        expected_revision=read.source_revision,
        confirmed=True,
        store=store,
    )
    assert edited.ok and edited.source_revision
    assert "# Stored edit" in store.read(target).source

    published = author_transition(
        "publish",
        "docs/get-started",
        mounts=mounts,
        subject=subject,
        expected_revision=edited.source_revision,
        confirmed=True,
        store=store,
    )
    assert published.ok

    created = author_new(
        "docs/virtual-draft",
        mounts=mounts,
        subject=subject,
        confirmed=True,
        store=store,
    )
    assert created.ok and created.target_path is not None
    assert store.exists(created.target_path) is True
    assert created.target_path.exists() is False
    assert target.read_text(encoding="utf-8") == disk_source


@pytest.mark.parametrize("backend", ("memory", "filesystem"))
def test_author_store_allows_only_one_simultaneous_compare_and_swap(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryAuthorMutationStore() if backend == "memory" else FilesystemAuthorMutationStore()
    )
    path = tmp_path / f"{backend}-race.md"
    initial = store.create(path, "# Initial\n")
    barrier = Barrier(2)

    def replace(source: str) -> str:
        barrier.wait()
        try:
            store.replace(path, source, expected_revision=initial.revision)
        except AuthorSourceConflict:
            return "conflict"
        return "replaced"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(replace, ("# Writer A\n", "# Writer B\n")))

    assert sorted(outcomes) == ["conflict", "replaced"]
    assert store.read(path).source in {"# Writer A\n", "# Writer B\n"}
