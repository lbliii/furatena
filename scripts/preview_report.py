#!/usr/bin/env python3
"""Verify a preview and idempotently publish one GitHub check and PR comment."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from furatena.catalog.preview_conformance import (  # noqa: E402
    PreviewConformanceResult,
    inspect_preview,
    preview_comment_markdown,
)

CHECK_NAME = "Furatena preview conformance"
COMMENT_MARKER = "<!-- furatena-preview -->"


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get(args.preview_token_env, "")
    result: PreviewConformanceResult | None = None
    state = args.state
    if state == "ready":
        if not args.origin or not token:
            state = "failed"
            remediation = "Configure the preview origin and reviewer token, then retry conformance."
        else:
            result = inspect_preview(args.origin, token, args.expected_sha)
            state = "ready" if result.ok else "failed"
            remediation = None
    else:
        remediation = args.remediation
    comment = preview_comment_markdown(
        state,
        args.expected_sha,
        result=result,
        remediation=remediation,
    )
    payload = {
        "state": state,
        "ok": result.ok if result is not None else state not in {"failed"},
        "comment": comment,
        "conformance": result.to_dict() if result is not None else None,
    }
    if args.output:
        Path(args.output).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if args.publish:
        github_token = os.environ.get(args.github_token_env, "")
        if not github_token:
            raise SystemExit(f"{args.github_token_env} is required with --publish")
        _publish(
            repository=args.repository,
            pr_number=args.pr_number,
            head_sha=args.expected_sha,
            state=state,
            comment=comment,
            token=github_token,
            details_url=args.details_url or args.origin,
            summary=_check_summary(state, result),
        )
    print(json.dumps({key: value for key, value in payload.items() if key != "comment"}, indent=2))
    return 0


def _publish(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    state: str,
    comment: str,
    token: str,
    details_url: str,
    summary: str,
) -> None:
    api = _GitHubAPI(repository, token)
    external_id = f"furatena-preview:{repository}:{pr_number}:{head_sha}"
    checks = api.get(
        f"/commits/{head_sha}/check-runs?check_name={urllib.parse.quote(CHECK_NAME)}"
    ).get("check_runs", [])
    existing = next(
        (item for item in checks if item.get("external_id") == external_id),
        None,
    )
    check_payload = _check_payload(state, head_sha, external_id, details_url, summary)
    if existing is None:
        api.post("/check-runs", check_payload)
    else:
        api.patch(f"/check-runs/{existing['id']}", check_payload)

    comments = api.get(f"/issues/{pr_number}/comments?per_page=100")
    existing_comment = next(
        (item for item in comments if COMMENT_MARKER in str(item.get("body") or "")),
        None,
    )
    if existing_comment is None:
        api.post(f"/issues/{pr_number}/comments", {"body": comment})
    else:
        api.patch(f"/issues/comments/{existing_comment['id']}", {"body": comment})


def _check_payload(
    state: str,
    head_sha: str,
    external_id: str,
    details_url: str,
    summary: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "external_id": external_id,
        "output": {"title": f"Furatena preview: {state}", "summary": summary},
    }
    if details_url:
        payload["details_url"] = details_url
    if state == "queued":
        payload["status"] = "queued"
    elif state in {"building", "requested"}:
        payload["status"] = "in_progress"
    else:
        payload["status"] = "completed"
        payload["conclusion"] = {
            "ready": "success",
            "failed": "failure",
            "removed": "neutral",
        }.get(state, "neutral")
    return payload


def _check_summary(state: str, result: PreviewConformanceResult | None) -> str:
    if result is None:
        return {
            "queued": "Preview request accepted for the current PR head.",
            "building": "Preview is building for the current PR head.",
            "removed": "Preview environment and review URLs were removed.",
            "failed": "Preview failed. See the idempotent PR comment for remediation.",
        }.get(state, f"Preview lifecycle state: {state}.")
    passed = sum(check.ok for check in result.checks)
    return f"{passed}/{len(result.checks)} human-and-agent conformance checks passed."


class _GitHubAPI:
    def __init__(self, repository: str, token: str) -> None:
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def get(self, path: str) -> Any:
        return self._request("GET", path, None)

    def post(self, path: str, payload: Mapping[str, object]) -> Any:
        return self._request("POST", path, payload)

    def patch(self, path: str, payload: Mapping[str, object]) -> Any:
        return self._request("PATCH", path, payload)

    def _request(self, method: str, path: str, payload: Mapping[str, object] | None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "furatena-preview-report/1",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state", choices=("queued", "building", "ready", "failed", "removed"), required=True
    )
    parser.add_argument("--origin", default="")
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--remediation", default=None)
    parser.add_argument("--preview-token-env", default="FURA_PREVIEW_AUTH_TOKEN")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--pr-number", type=int, default=0)
    parser.add_argument("--github-token-env", default="GITHUB_TOKEN")
    parser.add_argument("--details-url", default="")
    parser.add_argument("--output", default="")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
