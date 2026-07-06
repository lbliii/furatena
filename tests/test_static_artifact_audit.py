"""Static artifact URL crawler contracts."""

from __future__ import annotations

import json
from pathlib import Path

from furatena.catalog.artifact_audit import audit_static_artifacts, main

BASE_PATH = "/furatena"
SITE_URL = "https://example.github.io/furatena"


def _write_valid_artifact(root: Path) -> None:
    (root / "docs/guide").mkdir(parents=True)
    (root / "static").mkdir()
    (root / "index.html").write_text(
        """<!doctype html>
<html><head>
<link rel="canonical" href="https://example.github.io/furatena/">
<link rel="stylesheet" href="/furatena/static/app.css">
</head><body><a href="/furatena/docs/guide/" hx-push-url="true">Guide</a></body></html>
""",
        encoding="utf-8",
    )
    (root / "docs/guide/index.html").write_text(
        '<a href="/furatena/">Home</a>\n', encoding="utf-8"
    )
    (root / "static/app.css").write_text("body {}\n", encoding="utf-8")
    (root / "catalog.json").write_text(
        json.dumps(
            {
                "pages": [{"url": "/furatena/docs/guide/"}],
                "links": {"self": "https://example.github.io/furatena/catalog.json"},
                "artifacts": ["index.html", "docs/guide/index.html"],
                "planned": {"status": "planned", "url": "/furatena/pdf/"},
            }
        ),
        encoding="utf-8",
    )
    (root / "sitemap.xml").write_text(
        """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.github.io/furatena/docs/guide/</loc></url>
</urlset>
""",
        encoding="utf-8",
    )
    (root / "robots.txt").write_text(
        "Sitemap: https://example.github.io/furatena/sitemap.xml\n", encoding="utf-8"
    )


def test_audit_accepts_valid_html_sitemap_json_assets_and_text(tmp_path: Path) -> None:
    _write_valid_artifact(tmp_path)

    report = audit_static_artifacts(tmp_path, base_path=BASE_PATH, site_url=SITE_URL)

    assert report.ok
    assert len(report.references) >= 10


def test_audit_reports_repeated_and_escaped_prefix_with_referrer(tmp_path: Path) -> None:
    _write_valid_artifact(tmp_path)
    (tmp_path / "index.html").write_text(
        '<a href="/furatena/furatena/docs/guide/">Double</a>'
        '<a href="/docs/guide/">Escape</a>',
        encoding="utf-8",
    )

    report = audit_static_artifacts(tmp_path, base_path=BASE_PATH, site_url=SITE_URL)

    findings = {(item.code, item.source.as_posix(), item.referrer) for item in report.findings}
    assert ("repeated-base-path", "index.html", "/furatena/") in findings
    assert ("escaped-base-path", "index.html", "/furatena/") in findings


def test_audit_reports_wrong_canonical_origin_and_missing_sidecar_target(tmp_path: Path) -> None:
    _write_valid_artifact(tmp_path)
    (tmp_path / "index.html").write_text(
        '<link rel="canonical" href="https://wrong.example/furatena/">', encoding="utf-8"
    )
    (tmp_path / "catalog.json").write_text(
        json.dumps({"links": {"self": "/furatena/missing.json"}}), encoding="utf-8"
    )

    report = audit_static_artifacts(tmp_path, base_path=BASE_PATH, site_url=SITE_URL)

    by_code = {item.code: item for item in report.findings}
    assert by_code["canonical-origin"].source == Path("index.html")
    assert by_code["missing-target"].source == Path("catalog.json")
    assert by_code["missing-target"].target == "/furatena/missing.json"


def test_audit_resolves_relative_links_and_rejects_parent_traversal(tmp_path: Path) -> None:
    _write_valid_artifact(tmp_path)
    (tmp_path / "docs/guide/index.html").write_text(
        '<a href="../../">Home</a><a href="/furatena/../private/">Escape</a>',
        encoding="utf-8",
    )

    report = audit_static_artifacts(tmp_path, base_path=BASE_PATH, site_url=SITE_URL)

    assert [item.code for item in report.findings] == ["malformed-url"]
    assert report.findings[0].source == Path("docs/guide/index.html")


def test_module_cli_fails_with_source_and_referrer(tmp_path: Path, capsys) -> None:
    _write_valid_artifact(tmp_path)
    (tmp_path / "index.html").write_text('<img src="/furatena/static/missing.png">')

    exit_code = main(
        [str(tmp_path), "--base-path", BASE_PATH, "--site-url", SITE_URL]
    )

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "index.html (/furatena/)" in stderr
    assert "/furatena/static/missing.png" in stderr
