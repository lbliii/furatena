"""Cross-surface conformance for one commit-bound preview deployment."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.client import HTTPMessage
from typing import IO, Any

from furatena.catalog.preview_contracts import PreviewManifest, PreviewState


@dataclass(frozen=True, slots=True)
class PreviewHTTPResponse:
    status: int
    content_type: str
    body: bytes
    url: str


@dataclass(frozen=True, slots=True)
class PreviewConformanceCheck:
    check_id: str
    ok: bool
    summary: str
    remediation: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.check_id,
            "status": "pass" if self.ok else "fail",
            "summary": self.summary,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class PreviewConformanceResult:
    origin: str
    expected_sha: str
    manifest: PreviewManifest | None
    checks: tuple[PreviewConformanceCheck, ...]

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "kind": "preview_conformance",
            "ok": self.ok,
            "origin": self.origin,
            "expected_sha": self.expected_sha,
            "manifest": self.manifest.to_dict() if self.manifest is not None else None,
            "checks": [check.to_dict() for check in self.checks],
        }


PreviewFetcher = Callable[[str, str | None, str | None], PreviewHTTPResponse]


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit redirects only while they retain the original HTTPS origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            same_origin = _https_origin(req.full_url) == _https_origin(newurl)
        except ValueError as error:
            raise urllib.error.URLError(
                "Preview conformance refused an invalid redirect target before forwarding credentials."
            ) from error
        if not same_origin:
            raise urllib.error.URLError(
                "Preview conformance refused a cross-origin redirect before forwarding credentials."
            )
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


def inspect_preview(
    origin: str,
    token: str,
    expected_sha: str,
    *,
    fetch: PreviewFetcher | None = None,
) -> PreviewConformanceResult:
    """Verify human and agent surfaces against the preview manifest identity."""

    try:
        root, expected_origin = _preview_root(origin)
    except ValueError as error:
        return PreviewConformanceResult(
            origin.strip().rstrip("/"),
            expected_sha,
            None,
            (
                PreviewConformanceCheck(
                    check_id="origin",
                    ok=False,
                    summary=f"Preview origin could not be verified: {_safe_error(error)}",
                    remediation="Provide the exact HTTPS preview origin without a path or credentials.",
                ),
            ),
        )
    client = fetch or fetch_preview_url
    checks: list[PreviewConformanceCheck] = []
    manifest: PreviewManifest | None = None
    try:
        response = client(f"{root}/preview-manifest.json", token, "application/json")
        _require_origin(response.url, expected_origin)
        _require_response(response, media="application/json")
        manifest = PreviewManifest.from_dict(_json_object(response.body))
        checks.append(
            _check(
                "manifest-state",
                manifest.state == PreviewState.READY,
                f"Preview manifest state is {manifest.state.value}.",
                "Wait for a ready manifest before sharing the preview.",
            )
        )
        checks.append(
            _check(
                "immutable-head-sha",
                manifest.source.head_sha == expected_sha
                and manifest.artifact is not None
                and manifest.artifact.source_sha == expected_sha,
                f"Preview manifest reports head {manifest.source.head_sha}.",
                "Discard the stale deployment and rebuild the current PR head SHA.",
            )
        )
    except (ValueError, OSError, urllib.error.URLError) as error:
        checks.append(
            PreviewConformanceCheck(
                check_id="manifest",
                ok=False,
                summary=f"Preview manifest could not be verified: {_safe_error(error)}",
                remediation="Verify preview authentication, deployment state, and manifest route.",
            )
        )
        return PreviewConformanceResult(root, expected_sha, None, tuple(checks))

    surfaces = manifest.surfaces
    if surfaces is None:
        checks.append(
            PreviewConformanceCheck(
                "surfaces",
                False,
                "Ready manifest omitted preview surfaces.",
                "Rebuild with a complete provider manifest.",
            )
        )
        return PreviewConformanceResult(root, expected_sha, manifest, tuple(checks))

    surface_urls = (
        surfaces.human_url,
        surfaces.markdown_url_template.replace("{path}", "index"),
        surfaces.llms_url,
        surfaces.catalog_url,
        surfaces.query_url,
        surfaces.search_url,
        surfaces.metadata_url,
        surfaces.health_url,
        surfaces.readiness_url,
    )
    exact_origin = True
    try:
        exact_origin = all(_https_origin(url) == expected_origin for url in surface_urls)
    except ValueError:
        exact_origin = False
    checks.append(
        _check(
            "immutable-origin",
            exact_origin,
            (
                "Every advertised preview surface uses the requested HTTPS origin."
                if exact_origin
                else "The preview manifest advertises an invalid or cross-origin surface."
            ),
            "Rebuild the manifest with every surface bound to the allocated preview origin.",
        )
    )
    if not exact_origin:
        return PreviewConformanceResult(root, expected_sha, manifest, tuple(checks))

    readiness = client(surfaces.readiness_url, None, "application/json")
    try:
        _require_origin(readiness.url, expected_origin)
    except ValueError as error:
        checks.append(
            PreviewConformanceCheck(
                "readiness",
                False,
                f"Readiness origin could not be verified: {_safe_error(error)}",
                "Repair the readiness route so it remains on the allocated preview origin.",
            )
        )
        return PreviewConformanceResult(root, expected_sha, manifest, tuple(checks))
    readiness_body = _json_object(readiness.body) if readiness.status == 200 else {}
    checks.append(
        _check(
            "readiness",
            readiness.status == 200 and readiness_body.get("ok") is True,
            f"Readiness returned HTTP {readiness.status}.",
            "Inspect /readyz remediation and deployment logs before reporting ready.",
        )
    )

    probes = (
        ("html", surfaces.human_url, "text/html", "text/html"),
        ("negotiated-markdown", surfaces.human_url, "text/markdown", "text/markdown"),
        (
            "markdown-alias",
            surfaces.markdown_url_template.replace("{path}", "index"),
            "text/markdown",
            "text/markdown",
        ),
        ("llms", surfaces.llms_url, "text/plain", "text/plain"),
        ("catalog", surfaces.catalog_url, "application/json", "application/json"),
        ("catalog-query", surfaces.query_url, "application/json", "application/json"),
        ("search", surfaces.search_url, "application/json", "application/json"),
        ("metadata", surfaces.metadata_url, "application/json", "application/json"),
    )
    for check_id, url, accept, expected_media in probes:
        try:
            response = client(url, token, accept)
            _require_origin(response.url, expected_origin)
            ok = response.status == 200 and expected_media in response.content_type.lower()
            if ok and expected_media == "application/json":
                _json_object(response.body)
            checks.append(
                _check(
                    check_id,
                    ok,
                    f"{url} returned HTTP {response.status} as {response.content_type}.",
                    f"Repair the {check_id} surface and rebuild the reviewed SHA.",
                )
            )
        except (ValueError, OSError, urllib.error.URLError) as error:
            checks.append(
                PreviewConformanceCheck(
                    check_id,
                    False,
                    f"{check_id} probe failed: {_safe_error(error)}",
                    f"Repair the {check_id} surface and rebuild the reviewed SHA.",
                )
            )
    return PreviewConformanceResult(root, expected_sha, manifest, tuple(checks))


def preview_comment_markdown(
    state: str,
    expected_sha: str,
    *,
    result: PreviewConformanceResult | None = None,
    remediation: str | None = None,
) -> str:
    """Render the one marker-addressable GitHub PR comment."""

    normalized = state.strip().lower()
    icon = {"ready": "✅", "failed": "❌", "removed": "🧹"}.get(normalized, "⏳")
    lines = [
        "<!-- furatena-preview -->",
        f"## {icon} Furatena preview: {normalized}",
        "",
        f"Reviewed head: `{expected_sha}`",
    ]
    manifest = result.manifest if result is not None else None
    if normalized == "removed":
        lines.extend(["", "The ephemeral environment and its review URLs have been removed."])
    elif manifest is not None and manifest.surfaces is not None and manifest.artifact is not None:
        surfaces = manifest.surfaces
        lines.extend(
            [
                "",
                f"**[Open human preview]({surfaces.human_url})**",
                "",
                "Authorized reviewers: use Basic username `preview` with the separately shared "
                "preview token as password, or send it as a Bearer token. The token is never "
                "included in this comment.",
                "",
                "| Surface | Review link |",
                "| --- | --- |",
                f"| Negotiated Markdown | [{surfaces.human_url}]({surfaces.human_url}) with `Accept: text/markdown` |",
                f"| Markdown alias | [{surfaces.markdown_url_template.replace('{path}', 'index')}]({surfaces.markdown_url_template.replace('{path}', 'index')}) |",
                f"| `llms.txt` | [{surfaces.llms_url}]({surfaces.llms_url}) |",
                f"| Catalog | [{surfaces.catalog_url}]({surfaces.catalog_url}) |",
                f"| Catalog query | [{surfaces.query_url}]({surfaces.query_url}) |",
                f"| Search | [{surfaces.search_url}]({surfaces.search_url}) |",
                f"| Metadata | [{surfaces.metadata_url}]({surfaces.metadata_url}) |",
                "",
                f"Build `{manifest.artifact.build_id}` · freeze `{manifest.artifact.fingerprint}` · frozen `{manifest.artifact.frozen_at}`",
            ]
        )
    if result is not None:
        lines.extend(
            ["", "### Conformance", "", "| Check | Result | Summary |", "| --- | --- | --- |"]
        )
        for check in result.checks:
            lines.append(
                f"| `{check.check_id}` | {'pass' if check.ok else 'fail'} | {_table(check.summary)} |"
            )
        failures = [
            check.remediation for check in result.checks if not check.ok and check.remediation
        ]
        if failures:
            lines.extend(["", "Remediation: " + " ".join(dict.fromkeys(failures))])
    elif remediation:
        lines.extend(["", f"Remediation: {remediation}"])
    if manifest is not None:
        lines.extend(
            [
                "",
                "<details><summary>Provider diagnostics</summary>",
                "",
                f"- Provider: `{manifest.provider.provider_id}`",
                f"- Project: `{manifest.provider.project_id}`",
                f"- Environment: `{manifest.provider.environment_id}`",
                f"- Deployment: `{manifest.provider.deployment_id}`",
                f"- Manifest digest: `{manifest.manifest_digest}`",
                "",
                "</details>",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def fetch_preview_url(url: str, token: str | None, accept: str | None) -> PreviewHTTPResponse:
    headers = {"Accept": accept or "*/*", "User-Agent": "furatena-preview-conformance/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler())
    with opener.open(request, timeout=30) as response:
        return PreviewHTTPResponse(
            status=int(response.status),
            content_type=str(response.headers.get("Content-Type") or ""),
            body=response.read(),
            url=str(response.url),
        )


def _require_response(response: PreviewHTTPResponse, *, media: str) -> None:
    if response.status != 200 or media not in response.content_type.lower():
        raise ValueError(f"HTTP {response.status} as {response.content_type or 'unknown'}")


def _preview_root(value: str) -> tuple[str, tuple[str, str, int]]:
    candidate = value.strip()
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("preview origin must not contain a path, query, or fragment")
    origin = _https_origin(candidate)
    return candidate.rstrip("/"), origin


def _https_origin(value: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "%" in parsed.netloc
    ):
        raise ValueError("preview URL must use HTTPS without credentials")
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError("preview URL has an invalid port") from error
    return ("https", parsed.hostname.casefold(), port)


def _require_origin(url: str, expected: tuple[str, str, int]) -> None:
    if _https_origin(url) != expected:
        raise ValueError("response crossed the allocated preview origin")


def _json_object(body: bytes) -> Mapping[str, Any]:
    value = json.loads(body)
    if not isinstance(value, Mapping):
        raise ValueError("JSON response must be an object")
    return value


def _check(
    check_id: str,
    ok: bool,
    summary: str,
    remediation: str,
) -> PreviewConformanceCheck:
    return PreviewConformanceCheck(check_id, ok, summary, None if ok else remediation)


def _safe_error(error: BaseException) -> str:
    return str(error).replace("\n", " ")[:240]


def _table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
