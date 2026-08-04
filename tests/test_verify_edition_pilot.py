"""Deterministic contracts for the deployed edition-pilot verifier."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode

import pytest

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs" / "b-stack-pilot-v1.json"
BUILD_SHA = "a" * 40
CONTENT_REF = "b" * 40
IMAGE_DIGEST = f"sha256:{'c' * 64}"
GENERATION = "pilot-2026-08-04"


def _load_verifier() -> ModuleType:
    path = REPO / "scripts" / "verify-edition-pilot.py"
    spec = importlib.util.spec_from_file_location("furatena_verify_edition_pilot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _query(path: str, **params: str) -> str:
    return f"{path}?{urlencode(tuple(params.items()))}"


def _responses(verifier: ModuleType) -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    versions: dict[str, list[dict[str, object]]] = {}
    responses: dict[str, Any] = {}

    def response(
        status: int,
        payload: dict[str, object] | bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> Any:
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        return verifier.HTTPResponse(status, headers or {}, body)

    for record in manifest["mounts"]:
        mount_id = record["id"]
        entries: list[dict[str, object]] = [
            {
                "version": "latest",
                "title": "Latest",
                "aliases": ["latest", "stable"],
                "url_prefix": f"/{mount_id}",
            }
        ]
        entries.extend(
            {
                "version": release["edition"],
                "title": release["edition"],
                "aliases": [],
                "url_prefix": f"/v{release['edition']}/{mount_id}",
            }
            for release in record["releases"]
        )
        versions[mount_id] = entries

        selected = record["releases"][0]["edition"]
        latest_path = f"/{mount_id}/"
        historical_path = f"/v{selected}/{mount_id}/"
        responses[latest_path] = response(
            200,
            f'<a data-docs-version-target="{selected}" href="{historical_path}">old</a>'.encode(),
        )
        responses[historical_path] = response(
            200,
            (
                f'<div data-edition-status="legacy"><a href="{latest_path}">current</a></div>'
            ).encode(),
        )
        for alias in ("latest", "stable"):
            responses[f"/{alias}{latest_path}"] = response(
                301, b"", headers={"location": latest_path}
            )
        query_path = _query("/catalog/query.json", mount=mount_id, edition=selected)
        responses[query_path] = response(
            200,
            {
                "edition": selected,
                "pages": [{"mount": mount_id, "edition": selected}],
            },
        )
        semantic_path = _query("/search/semantic", q=mount_id, mount=mount_id, edition=selected)
        responses[semantic_path] = response(
            200,
            {
                "filters": {"mount": mount_id, "edition": selected},
                "results": [{"mount": mount_id, "edition": selected}],
            },
        )

    responses["/versions.json"] = response(200, {"schema_version": 1, "mounts": versions})
    checks = [
        {"id": f"{kind}:{mount_id}", "ok": True, "status": "pass"}
        for mount_id in versions
        for kind in ("source", "index")
    ]
    responses["/readyz"] = response(
        200,
        {"schema_version": 1, "ok": True, "status": "ready", "checks": checks},
    )
    responses["/meta.json"] = response(
        200,
        {
            "build": {
                "git_sha": BUILD_SHA,
                "content": {
                    "generation": GENERATION,
                    "selected_generation": GENERATION,
                    "resolved_ref": CONTENT_REF,
                    "activation_pending_restart": False,
                },
                "image": {"digest": IMAGE_DIGEST},
                "freeze_fingerprint": "d" * 64,
            }
        },
    )
    responses["/v0.9.0/pounce/"] = response(200, b'<div data-edition-status="legacy">pre-fix</div>')
    responses["/v0.9.2/pounce/"] = response(
        200, b'<div data-edition-status="legacy"><a href="/pounce/">fixed</a></div>'
    )
    diff_path = _query("/catalog/diff", mount="pounce", **{"from": "0.9.0", "to": "0.9.2"})
    responses[diff_path] = response(
        200,
        {
            "schema_version": 1,
            "ok": True,
            "kind": "mount",
            "mount": "pounce",
            "from": {"edition": "0.9.0"},
            "to": {"edition": "0.9.2"},
            "total": 2,
            "pages": [{"slug": "deployment"}, {"slug": "runtime"}],
        },
    )
    return responses


def _verify(verifier: ModuleType) -> dict[str, Any]:
    return verifier.verify_edition_pilot(
        "https://pilot.example.test",
        manifest_path=MANIFEST,
        expected_build_sha=BUILD_SHA,
        expected_content_generation=GENERATION,
        expected_content_ref=CONTENT_REF,
        expected_image_digest=IMAGE_DIGEST,
    )


def test_receipt_is_deterministic_and_covers_the_full_pilot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verifier = _load_verifier()
    responses = _responses(verifier)

    def request(origin: str, path: str, *, timeout: float) -> Any:
        assert origin == "https://pilot.example.test"
        assert timeout == 120.0
        return responses[path]

    monkeypatch.setattr(verifier, "_request", request)

    first = _verify(verifier)
    second = _verify(verifier)

    assert first == second
    assert first["schema_version"] == 1
    assert first["ok"] is True
    assert len(first["mounts"]) == 8
    assert {item["mount"] for item in first["mounts"]} == {
        "bengal",
        "chirp",
        "chirp-ui",
        "kida",
        "patitas",
        "pounce",
        "rosettes",
        "zoomies",
    }
    assert first["readiness"]["required_check_count"] == 16
    assert first["identity"] == {
        "build_git_sha": BUILD_SHA,
        "content_generation": GENERATION,
        "content_resolved_ref": CONTENT_REF,
        "image_digest": IMAGE_DIGEST,
        "freeze_fingerprint": "d" * 64,
    }
    assert first["pounce_cross_version"] == {
        "mount": "pounce",
        "from": "0.9.0",
        "to": "0.9.2",
        "from_path": "/v0.9.0/pounce/",
        "to_path": "/v0.9.2/pounce/",
        "diff_total": 2,
        "delivered_page_count": 2,
    }
    output = tmp_path / "evidence" / "receipt.json"
    verifier._write_receipt(output, first)
    assert json.loads(output.read_text(encoding="utf-8")) == first
    assert "observed_at" not in output.read_text(encoding="utf-8")


def test_identity_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    verifier = _load_verifier()
    responses = _responses(verifier)
    bad_meta = copy.deepcopy(responses["/meta.json"])
    payload = json.loads(bad_meta.body)
    payload["build"]["git_sha"] = "e" * 40
    responses["/meta.json"] = verifier.HTTPResponse(200, {}, json.dumps(payload).encode())
    monkeypatch.setattr(
        verifier,
        "_request",
        lambda origin, path, *, timeout: responses[path],
    )

    with pytest.raises(RuntimeError, match="identity does not match"):
        _verify(verifier)


def test_scoped_search_result_cannot_escape_its_edition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    responses = _responses(verifier)
    path = _query("/search/semantic", q="bengal", mount="bengal", edition="0.5.1")
    responses[path] = verifier.HTTPResponse(
        200,
        {},
        json.dumps(
            {
                "filters": {"mount": "bengal", "edition": "0.5.1"},
                "results": [{"mount": "chirp", "edition": "latest"}],
            }
        ).encode(),
    )
    monkeypatch.setattr(
        verifier,
        "_request",
        lambda origin, requested, *, timeout: responses[requested],
    )

    with pytest.raises(RuntimeError, match="search results escaped"):
        _verify(verifier)


def test_pounce_story_requires_a_nonempty_structural_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verifier = _load_verifier()
    responses = _responses(verifier)
    path = _query("/catalog/diff", mount="pounce", **{"from": "0.9.0", "to": "0.9.2"})
    payload = json.loads(responses[path].body)
    payload.update(total=0, pages=[])
    responses[path] = verifier.HTTPResponse(200, {}, json.dumps(payload).encode())
    monkeypatch.setattr(
        verifier,
        "_request",
        lambda origin, requested, *, timeout: responses[requested],
    )

    with pytest.raises(RuntimeError, match="non-empty pounce"):
        _verify(verifier)


def test_origin_rejects_credentials_and_non_https() -> None:
    verifier = _load_verifier()

    for origin in ("http://pilot.example.test", "https://token@pilot.example.test"):
        with pytest.raises(ValueError, match="credential-free HTTPS"):
            verifier.verify_edition_pilot(
                origin,
                manifest_path=MANIFEST,
                expected_build_sha=BUILD_SHA,
                expected_content_generation=GENERATION,
                expected_content_ref=CONTENT_REF,
                expected_image_digest=IMAGE_DIGEST,
            )
