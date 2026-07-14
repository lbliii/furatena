"""Pull-request preview identity, access, and anti-indexing coverage."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest
from chirp.testing import TestClient

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.preview_security import (
    PreviewAccessCredentials,
    PreviewConfigurationError,
    PreviewEnvironment,
)
from furatena.catalog.runtime import ServeConfig, ServeMode
from furatena.catalog.seo import docs_base_url
from tests.support import copy_app_theme, write_minimal_docs_yaml, write_mounts_yaml

REPO = Path(__file__).resolve().parents[1]
PREVIEW_SHA = "a" * 40
PREVIEW_TOKEN = "review-token-0123456789-abcdefghijklmnop"


def _preview_environment() -> dict[str, str]:
    return {
        "FURA_PR_PREVIEW": "1",
        "FURA_PREVIEW_PR_NUMBER": "423",
        "FURA_PREVIEW_SHA": PREVIEW_SHA,
        "FURA_BUILD_GIT_SHA": PREVIEW_SHA,
        "FURA_PREVIEW_REVIEW_URL": "https://github.com/lbliii/furatena/pull/423",
        "RAILWAY_PUBLIC_DOMAIN": "furatena-pr-423.up.railway.app",
        "FURA_PREVIEW_AUTH_TOKEN": PREVIEW_TOKEN,
    }


def _configure_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _preview_environment().items():
        monkeypatch.setenv(name, value)


def _docs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DocsApp:
    _configure_preview(monkeypatch)
    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    (content_root / "_index.md").write_text(
        "---\ntitle: Preview home\n---\n\n# Preview home\n",
        encoding="utf-8",
    )
    (content_root / "private.md").write_text(
        "---\ntitle: Private canary\nvisibility: private\n---\n\nPRIVATE_PREVIEW_CANARY\n",
        encoding="utf-8",
    )
    return DocsApp.from_paths(
        app_root / "docs.yaml",
        repo_root=tmp_path,
        autodoc=False,
        serve=ServeConfig(ServeMode.PREVIEW, None, False, False),
    )


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {PREVIEW_TOKEN}"}


def _basic() -> dict[str, str]:
    value = base64.b64encode(f"preview:{PREVIEW_TOKEN}".encode()).decode()
    return {"Authorization": f"Basic {value}"}


def test_preview_environment_is_explicit_commit_bound_and_secret_free() -> None:
    preview = PreviewEnvironment.from_environment(_preview_environment())

    assert preview is not None
    assert preview.pull_request_number == 423
    assert preview.head_sha == PREVIEW_SHA
    assert preview.short_sha == PREVIEW_SHA[:12]
    assert preview.origin == "https://furatena-pr-423.up.railway.app"
    assert PREVIEW_TOKEN not in repr(preview)
    assert PREVIEW_TOKEN not in str(preview.template_context)
    assert PREVIEW_TOKEN not in repr(
        PreviewAccessCredentials.from_environment(_preview_environment())
    )


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("FURA_PREVIEW_PR_NUMBER", "0", "positive integer"),
        ("FURA_PREVIEW_SHA", "main", "hexadecimal SHA"),
        ("FURA_PREVIEW_REVIEW_URL", "http://example.test/423", "HTTPS URL"),
        ("RAILWAY_PUBLIC_DOMAIN", "http://example.test", "HTTPS origin"),
    ],
)
def test_preview_environment_rejects_unsafe_identity(
    name: str,
    value: str,
    message: str,
) -> None:
    environ = _preview_environment()
    environ[name] = value

    with pytest.raises(PreviewConfigurationError, match=message):
        PreviewEnvironment.from_environment(environ)


def test_preview_environment_rejects_build_sha_drift() -> None:
    environ = _preview_environment()
    environ["FURA_BUILD_GIT_SHA"] = "b" * 40

    with pytest.raises(PreviewConfigurationError, match="immutable FURA_BUILD_GIT_SHA"):
        PreviewEnvironment.from_environment(environ)


def test_preview_origin_overrides_inherited_production_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_preview(monkeypatch)
    monkeypatch.setenv("FURA_BASE_URL", "https://docs.example.com")

    assert docs_base_url("internal:8000") == "https://furatena-pr-423.up.railway.app"


def test_explicit_preview_identity_requires_frozen_preview_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_preview(monkeypatch)
    app_root = tmp_path / "app"
    content_root = tmp_path / "content"
    app_root.mkdir()
    content_root.mkdir()
    copy_app_theme(app_root, REPO / "app")
    write_minimal_docs_yaml(app_root / "docs.yaml")
    write_mounts_yaml(app_root / "mounts.yaml", content_root)
    (content_root / "_index.md").write_text("# Home\n", encoding="utf-8")

    with pytest.raises(PreviewConfigurationError, match="frozen preview mode"):
        DocsApp.from_paths(
            app_root / "docs.yaml",
            repo_root=tmp_path,
            autodoc=False,
            serve=ServeConfig(ServeMode.AUTHOR, None, False, True),
        )


def test_review_auth_covers_html_machine_routes_and_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _docs(tmp_path, monkeypatch)
    client = TestClient(docs.create_app())

    async def verify() -> None:
        for path in ("/", "/catalog.json", "/llms.txt", "/docs-theme/branding/favicon.svg"):
            denied = await client.get(path)
            assert denied.status == 401
            assert denied.header("WWW-Authenticate") is not None
            assert denied.header("X-Robots-Tag") == ("noindex, nofollow, noarchive, nosnippet")
            assert denied.header("Cache-Control") == "private, no-store"
            assert denied.header("Vary") == "Authorization"

            allowed = await client.get(path, headers=_bearer())
            assert allowed.status == 200
            assert allowed.header("X-Robots-Tag") == ("noindex, nofollow, noarchive, nosnippet")
            assert allowed.header("Cache-Control") == "private, no-store"
            assert "Authorization" in (allowed.header("Vary") or "")

        basic = await client.get("/", headers=_basic())
        assert basic.status == 200

    asyncio.run(verify())


def test_preview_html_has_dynamic_metadata_banner_and_no_private_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FURA_BASE_URL", "https://docs.example.com")
    docs = _docs(tmp_path, monkeypatch)
    client = TestClient(docs.create_app())

    async def fetch() -> tuple[str, str]:
        page = await client.get("/", headers=_bearer())
        catalog = await client.get("/catalog.json", headers=_bearer())
        assert page.status == catalog.status == 200
        return page.text, catalog.text

    page, catalog = asyncio.run(fetch())
    assert '<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">' in page
    assert 'data-fura-pr-preview="423"' in page
    assert f'data-fura-preview-sha="{PREVIEW_SHA}"' in page
    assert "pull request #423" in page
    assert PREVIEW_SHA[:12] in page
    assert "https://furatena-pr-423.up.railway.app/" in page
    assert "https://docs.example.com" not in page
    assert PREVIEW_TOKEN not in page
    assert "PRIVATE_PREVIEW_CANARY" not in page
    assert "PRIVATE_PREVIEW_CANARY" not in catalog
    assert "Private canary" not in catalog


def test_operational_probes_are_minimal_unauthenticated_and_noindex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _docs(tmp_path, monkeypatch)
    client = TestClient(docs.create_app())

    async def verify() -> None:
        for path in ("/healthz", "/readyz"):
            response = await client.get(path)
            assert response.status in {200, 503}
            assert response.header("X-Robots-Tag") == ("noindex, nofollow, noarchive, nosnippet")
            assert PREVIEW_TOKEN not in response.text
            assert "Private canary" not in response.text

    asyncio.run(verify())
