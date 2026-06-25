"""Tests for Wave F — MDX migration to canonical markdown."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APP_ROOT = REPO / "app"

sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.migrate.mdx import migrate_mdx_body, migrate_mdx_file, migrate_mounts
from furatena.catalog.registry import MountConfig


class TestMdxMigration:
    def test_lowers_jsx_block_to_directive(self) -> None:
        body = "<Note>\nWatch out\n</Note>\n"
        converted, unmigrated = migrate_mdx_body(body)
        assert unmigrated == ()
        assert ":::note" in converted
        assert "Watch out" in converted

    def test_migrate_file_writes_md_and_removes_mdx(self, tmp_path: Path) -> None:
        mdx = tmp_path / "docs" / "page.mdx"
        mdx.parent.mkdir(parents=True)
        mdx.write_text(
            "---\ntitle: Page\n---\n\n<Tip>Helpful</Tip>\n",
            encoding="utf-8",
        )
        report = migrate_mdx_file(mdx, write=True, remove_source=True)
        assert report.written
        assert report.removed_source
        assert not mdx.is_file()
        target = tmp_path / "docs" / "page.md"
        assert target.is_file()
        text = target.read_text(encoding="utf-8")
        assert ":::tip" in text
        assert "Helpful" in text

    def test_dry_run_does_not_write(self, tmp_path: Path) -> None:
        mdx = tmp_path / "hello.mdx"
        mdx.write_text("<Note>Hi</Note>\n", encoding="utf-8")
        report = migrate_mdx_file(mdx, write=False)
        assert not report.written
        assert mdx.is_file()
        assert not mdx.with_suffix(".md").is_file()

    def test_migrate_mounts_scans_content_roots(self, tmp_path: Path) -> None:
        root = tmp_path / "content"
        docs = root / "docs"
        docs.mkdir(parents=True)
        (docs / "one.mdx").write_text("<Note>One</Note>\n", encoding="utf-8")
        mounts = (
            MountConfig(
                id="chirp",
                label="Chirp",
                content_root=root,
                default=True,
            ),
        )
        reports = migrate_mounts(mounts, write=True)
        assert len(reports) == 1
        assert (docs / "one.md").is_file()
        assert not (docs / "one.mdx").is_file()
