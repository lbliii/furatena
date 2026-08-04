#!/usr/bin/env python3
"""Verify the deployed eight-mount edition pilot and write a deterministic receipt."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlencode, urlsplit

_EXPECTED_MOUNT_COUNT = 8
_POUNCE_FROM = "0.9.0"
_POUNCE_TO = "0.9.2"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_GENERATION = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LIFECYCLE = re.compile(rb'data-edition-status="(legacy|deprecated|preview|eol)"')


class HTTPResponse(NamedTuple):
    """One complete HTTP response used by the verifier."""

    status: int
    headers: dict[str, str]
    body: bytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _origin(value: str) -> str:
    normalized = value.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("origin must be a credential-free HTTPS origin with no path or query")
    return normalized


def _request(origin: str, path: str, *, timeout: float) -> HTTPResponse:
    request = urllib.request.Request(
        f"{origin}{path}",
        headers={"Accept-Encoding": "identity", "User-Agent": "furatena-edition-pilot/1"},
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        try:
            body = response.read()
        except http.client.IncompleteRead as exc:
            raise RuntimeError(
                f"{path}: response ended after {len(exc.partial)} bytes; "
                f"{exc.expected} more bytes were expected"
            ) from exc
        content_length = response.headers.get("Content-Length")
        if content_length is not None and len(body) != int(content_length):
            raise RuntimeError(
                f"{path}: received {len(body)} bytes; Content-Length is {content_length}"
            )
        status = response.status
        if not isinstance(status, int):
            raise RuntimeError(f"{path}: response did not include an HTTP status")
        return HTTPResponse(
            status,
            {str(key).lower(): str(value) for key, value in response.headers.items()},
            body,
        )


def _fetch_many(origin: str, paths: list[str], *, timeout: float) -> dict[str, HTTPResponse]:
    unique_paths = tuple(dict.fromkeys(paths))
    with ThreadPoolExecutor(max_workers=min(8, len(unique_paths))) as executor:
        futures = {
            path: executor.submit(_request, origin, path, timeout=timeout) for path in unique_paths
        }
        return {path: futures[path].result() for path in unique_paths}


def _json(path: str, response: HTTPResponse, *, status: int = 200) -> dict[str, Any]:
    if response.status != status:
        raise RuntimeError(f"{path}: expected HTTP {status}, received {response.status}")
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path}: response is not complete JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path}: expected a JSON object")
    return payload


def _load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{path}: pilot manifest is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"{path}: expected the v1 pilot manifest")
    mounts = payload.get("mounts")
    if not isinstance(mounts, list) or len(mounts) != _EXPECTED_MOUNT_COUNT:
        raise RuntimeError(f"{path}: expected exactly {_EXPECTED_MOUNT_COUNT} pilot mounts")
    ids = [item.get("id") for item in mounts if isinstance(item, dict)]
    if (
        len(ids) != _EXPECTED_MOUNT_COUNT
        or not all(isinstance(mount_id, str) and mount_id for mount_id in ids)
        or len(set(ids)) != _EXPECTED_MOUNT_COUNT
    ):
        raise RuntimeError(f"{path}: mount ids must be unique strings")
    for item in mounts:
        releases = item.get("releases") if isinstance(item, dict) else None
        if (
            not isinstance(releases, list)
            or not releases
            or not all(
                isinstance(release, dict)
                and isinstance(release.get("edition"), str)
                and release["edition"]
                for release in releases
            )
        ):
            raise RuntimeError(f"{path}: every pilot mount must record at least one release")
    pounce = next((item for item in mounts if item.get("id") == "pounce"), None)
    pounce_editions = {release.get("edition") for release in (pounce or {}).get("releases", [])}
    if not {_POUNCE_FROM, _POUNCE_TO} <= pounce_editions:
        raise RuntimeError(f"{path}: pounce releases must include {_POUNCE_FROM} and {_POUNCE_TO}")
    return payload, hashlib.sha256(raw).hexdigest()


def _version_entries(
    payload: dict[str, Any], manifest: dict[str, Any]
) -> dict[str, dict[str, dict[str, Any]]]:
    if payload.get("schema_version") != 1 or not isinstance(payload.get("mounts"), dict):
        raise RuntimeError("/versions.json: expected the mount-keyed v1 versions manifest")
    expected = {str(item["id"]): item for item in manifest["mounts"]}
    delivered = payload["mounts"]
    if set(delivered) != set(expected):
        raise RuntimeError("/versions.json: delivered mount ids do not match the pilot manifest")
    indexed: dict[str, dict[str, dict[str, Any]]] = {}
    for mount_id, record in expected.items():
        entries = delivered[mount_id]
        if not isinstance(entries, list):
            raise RuntimeError(f"/versions.json: {mount_id} editions must be an array")
        by_version = {str(item.get("version")): item for item in entries if isinstance(item, dict)}
        expected_editions = {"latest", *(str(item["edition"]) for item in record["releases"])}
        if set(by_version) != expected_editions:
            raise RuntimeError(
                f"/versions.json: {mount_id} editions do not match the pinned pilot manifest"
            )
        for edition, item in by_version.items():
            prefix = item.get("url_prefix")
            aliases = item.get("aliases")
            if (
                not isinstance(prefix, str)
                or not prefix.startswith("/")
                or "?" in prefix
                or "#" in prefix
                or not isinstance(aliases, list)
                or not all(isinstance(alias, str) for alias in aliases)
            ):
                raise RuntimeError(
                    f"/versions.json: {mount_id}:{edition} has invalid routing metadata"
                )
        indexed[mount_id] = by_version
    return indexed


def _alias_target(entries: dict[str, dict[str, Any]], alias: str) -> str:
    matches = [item for item in entries.values() if alias in item.get("aliases", [])]
    if len(matches) != 1:
        raise RuntimeError(f"/versions.json: alias {alias!r} must have exactly one target")
    return str(matches[0]["url_prefix"])


def _slash(path: str) -> str:
    return f"{path.rstrip('/')}/"


def _query_path(path: str, **params: str) -> str:
    return f"{path}?{urlencode(tuple(params.items()))}"


def _require_route(path: str, response: HTTPResponse) -> bytes:
    if response.status != 200:
        raise RuntimeError(f"{path}: expected HTTP 200, received {response.status}")
    if not response.body.strip():
        raise RuntimeError(f"{path}: response is empty")
    return response.body


def _require_redirect(path: str, response: HTTPResponse, target: str) -> None:
    if response.status != 301 or response.headers.get("location") != target:
        raise RuntimeError(f"{path}: expected HTTP 301 redirect to {target}")


def _validate_identity(
    payload: dict[str, Any],
    *,
    expected_build_sha: str,
    expected_content_generation: str,
    expected_content_ref: str,
    expected_image_digest: str,
) -> dict[str, Any]:
    build = payload.get("build")
    if not isinstance(build, dict):
        raise RuntimeError("/meta.json: build identity is missing")
    content = build.get("content")
    image = build.get("image")
    if not isinstance(content, dict) or not isinstance(image, dict):
        raise RuntimeError("/meta.json: content or image identity is missing")
    expected = {
        "build_git_sha": expected_build_sha,
        "content_generation": expected_content_generation,
        "content_resolved_ref": expected_content_ref,
        "image_digest": expected_image_digest,
    }
    actual = {
        "build_git_sha": build.get("git_sha"),
        "content_generation": content.get("generation"),
        "content_resolved_ref": content.get("resolved_ref"),
        "image_digest": image.get("digest"),
    }
    if actual != expected:
        raise RuntimeError(
            "/meta.json: deployed build/content identity does not match expectations"
        )
    if content.get("selected_generation") not in {None, expected_content_generation}:
        raise RuntimeError("/meta.json: selected content generation differs from the running one")
    if content.get("activation_pending_restart") is True:
        raise RuntimeError("/meta.json: content activation is pending restart")
    fingerprint = build.get("freeze_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        raise RuntimeError("/meta.json: freeze_fingerprint is not an exact SHA-256 identity")
    return {**actual, "freeze_fingerprint": fingerprint}


def _validate_readiness(payload: dict[str, Any], mount_ids: list[str]) -> dict[str, int]:
    if payload.get("ok") is not True or payload.get("status") != "ready":
        raise RuntimeError("/readyz: pilot is not ready")
    checks = payload.get("checks")
    if not isinstance(checks, list):
        raise RuntimeError("/readyz: readiness checks are missing")
    by_id = {str(item.get("id")): item for item in checks if isinstance(item, dict)}
    required = {f"{kind}:{mount_id}" for mount_id in mount_ids for kind in ("source", "index")}
    missing = sorted(required - set(by_id))
    failed = sorted(
        check_id for check_id in required if by_id.get(check_id, {}).get("ok") is not True
    )
    if missing or failed:
        raise RuntimeError(
            f"/readyz: pilot mount checks are incomplete; missing={missing}, failed={failed}"
        )
    return {"required_check_count": len(required), "delivered_check_count": len(checks)}


def _mount_paths(
    manifest: dict[str, Any], versions: dict[str, dict[str, dict[str, Any]]]
) -> list[str]:
    paths: list[str] = []
    for record in manifest["mounts"]:
        mount_id = str(record["id"])
        release = str(record["releases"][0]["edition"])
        latest_path = _slash(str(versions[mount_id]["latest"]["url_prefix"]))
        historical_path = _slash(str(versions[mount_id][release]["url_prefix"]))
        paths.extend(
            (
                latest_path,
                historical_path,
                f"/latest{latest_path}",
                f"/stable{latest_path}",
                _query_path("/catalog/query.json", mount=mount_id, edition=release),
                _query_path("/search/semantic", q=mount_id, mount=mount_id, edition=release),
            )
        )
    pounce = versions["pounce"]
    paths.extend(
        (
            _slash(str(pounce[_POUNCE_FROM]["url_prefix"])),
            _slash(str(pounce[_POUNCE_TO]["url_prefix"])),
            _query_path(
                "/catalog/diff",
                mount="pounce",
                **{"from": _POUNCE_FROM, "to": _POUNCE_TO},
            ),
        )
    )
    return paths


def _validate_mounts(
    manifest: dict[str, Any],
    versions: dict[str, dict[str, dict[str, Any]]],
    responses: dict[str, HTTPResponse],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in manifest["mounts"]:
        mount_id = str(record["id"])
        release = str(record["releases"][0]["edition"])
        entries = versions[mount_id]
        latest_path = _slash(str(entries["latest"]["url_prefix"]))
        historical_path = _slash(str(entries[release]["url_prefix"]))
        latest_body = _require_route(latest_path, responses[latest_path])
        historical_body = _require_route(historical_path, responses[historical_path])
        if (
            historical_path.encode() not in latest_body
            or latest_path.encode() not in historical_body
        ):
            raise RuntimeError(
                f"{mount_id}: edition selector does not round-trip latest and {release}"
            )
        lifecycle = _LIFECYCLE.search(historical_body)
        if lifecycle is None:
            raise RuntimeError(f"{historical_path}: historical lifecycle banner is missing")

        alias_targets: dict[str, str] = {}
        for alias in ("latest", "stable"):
            alias_path = f"/{alias}{latest_path}"
            target = _slash(_alias_target(entries, alias))
            _require_redirect(alias_path, responses[alias_path], target)
            alias_targets[alias] = target

        query_path = _query_path("/catalog/query.json", mount=mount_id, edition=release)
        query = _json(query_path, responses[query_path])
        pages = query.get("pages")
        if (
            query.get("edition") != release
            or not isinstance(pages, list)
            or not pages
            or any(
                item.get("mount") != mount_id or item.get("edition") != release
                for item in pages
                if isinstance(item, dict)
            )
            or any(not isinstance(item, dict) for item in pages)
        ):
            raise RuntimeError(f"{query_path}: catalog results escaped the edition context")

        semantic_path = _query_path("/search/semantic", q=mount_id, mount=mount_id, edition=release)
        semantic = _json(semantic_path, responses[semantic_path])
        hits = semantic.get("results")
        if (
            semantic.get("filters", {}).get("mount") != mount_id
            or semantic.get("filters", {}).get("edition") != release
            or not isinstance(hits, list)
            or not hits
            or any(
                item.get("mount") != mount_id or item.get("edition") != release
                for item in hits
                if isinstance(item, dict)
            )
            or any(not isinstance(item, dict) for item in hits)
        ):
            raise RuntimeError(f"{semantic_path}: search results escaped the edition context")

        results.append(
            {
                "mount": mount_id,
                "edition": release,
                "latest_path": latest_path,
                "historical_path": historical_path,
                "aliases": alias_targets,
                "lifecycle_status": lifecycle.group(1).decode(),
                "catalog_result_count": len(pages),
                "semantic_result_count": len(hits),
            }
        )
    return results


def _validate_pounce_story(
    versions: dict[str, dict[str, dict[str, Any]]], responses: dict[str, HTTPResponse]
) -> dict[str, Any]:
    entries = versions["pounce"]
    old_path = _slash(str(entries[_POUNCE_FROM]["url_prefix"]))
    fixed_path = _slash(str(entries[_POUNCE_TO]["url_prefix"]))
    _require_route(old_path, responses[old_path])
    _require_route(fixed_path, responses[fixed_path])
    diff_path = _query_path(
        "/catalog/diff",
        mount="pounce",
        **{"from": _POUNCE_FROM, "to": _POUNCE_TO},
    )
    payload = _json(diff_path, responses[diff_path])
    pages = payload.get("pages")
    if (
        payload.get("ok") is not True
        or payload.get("kind") != "mount"
        or payload.get("mount") != "pounce"
        or payload.get("from", {}).get("edition") != _POUNCE_FROM
        or payload.get("to", {}).get("edition") != _POUNCE_TO
        or not isinstance(payload.get("total"), int)
        or payload["total"] <= 0
        or not isinstance(pages, list)
        or not pages
    ):
        raise RuntimeError(f"{diff_path}: expected a non-empty pounce cross-version IR diff")
    return {
        "mount": "pounce",
        "from": _POUNCE_FROM,
        "to": _POUNCE_TO,
        "from_path": old_path,
        "to_path": fixed_path,
        "diff_total": payload["total"],
        "delivered_page_count": len(pages),
    }


def verify_edition_pilot(
    origin: str,
    *,
    manifest_path: Path,
    expected_build_sha: str,
    expected_content_generation: str,
    expected_content_ref: str,
    expected_image_digest: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Verify the live pilot and return stable, timestamp-free evidence."""
    origin = _origin(origin)
    if not _SHA.fullmatch(expected_build_sha):
        raise ValueError("expected build SHA must be 40 lowercase hexadecimal characters")
    if not _GENERATION.fullmatch(expected_content_generation):
        raise ValueError("expected content generation has an invalid identifier")
    if not _SHA.fullmatch(expected_content_ref):
        raise ValueError("expected content ref must be 40 lowercase hexadecimal characters")
    if not _SHA256.fullmatch(expected_image_digest):
        raise ValueError("expected image digest must be an exact sha256 digest")

    manifest, manifest_sha256 = _load_manifest(manifest_path)
    base = _fetch_many(origin, ["/versions.json", "/readyz", "/meta.json"], timeout=timeout)
    versions = _version_entries(_json("/versions.json", base["/versions.json"]), manifest)
    mount_ids = [str(item["id"]) for item in manifest["mounts"]]
    readiness = _validate_readiness(_json("/readyz", base["/readyz"]), mount_ids)
    identity = _validate_identity(
        _json("/meta.json", base["/meta.json"]),
        expected_build_sha=expected_build_sha,
        expected_content_generation=expected_content_generation,
        expected_content_ref=expected_content_ref,
        expected_image_digest=expected_image_digest,
    )
    responses = _fetch_many(origin, _mount_paths(manifest, versions), timeout=timeout)
    mounts = _validate_mounts(manifest, versions, responses)
    pounce_story = _validate_pounce_story(versions, responses)
    return {
        "schema_version": 1,
        "kind": "furatena-edition-pilot-smoke",
        "ok": True,
        "origin": origin,
        "pilot_manifest_sha256": manifest_sha256,
        "identity": identity,
        "readiness": readiness,
        "mounts": mounts,
        "pounce_cross_version": pounce_story,
    }


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"{json.dumps(receipt, indent=2, sort_keys=True)}\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Credential-free HTTPS origin of the edition pilot")
    parser.add_argument("--manifest", type=Path, default=Path("docs/b-stack-pilot-v1.json"))
    parser.add_argument("--expected-build-sha", required=True)
    parser.add_argument("--expected-content-generation", required=True)
    parser.add_argument("--expected-content-ref", required=True)
    parser.add_argument("--expected-image-digest", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    try:
        receipt = verify_edition_pilot(
            args.origin,
            manifest_path=args.manifest,
            expected_build_sha=args.expected_build_sha,
            expected_content_generation=args.expected_content_generation,
            expected_content_ref=args.expected_content_ref,
            expected_image_digest=args.expected_image_digest,
            timeout=args.timeout,
        )
        _write_receipt(args.output, receipt)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
