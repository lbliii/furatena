#!/usr/bin/env python3
"""Validate the checked-in Railway private-image template composer contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIGEST = re.compile(r"^ghcr\.io/lbliii/furatena@sha256:[0-9a-f]{64}$")


def validate_template_spec(spec: dict[str, Any], *, require_digest: bool = False) -> list[str]:
    failures: list[str] = []
    template = spec.get("template") or {}
    if template.get("source_type") != "private-docker-image":
        failures.append("service source must be a private Docker image")
    source = str(template.get("source") or "")
    if require_digest and DIGEST.fullmatch(source) is None:
        failures.append("published template source must use an exact promoted GHCR digest")
    if not template.get("hidden_registry_credentials"):
        failures.append("hidden registry credentials must be enabled")
    if template.get("healthcheck_path") != "/readyz":
        failures.append("healthcheck path must be /readyz")
    if int(template.get("replicas") or 0) != 1:
        failures.append("v1 template must use exactly one replica")
    volume = template.get("volume") or {}
    if volume.get("mount_path") != "/data/furatena" or not volume.get("required"):
        failures.append("required content volume must mount at /data/furatena")
    variables = {item.get("name"): item for item in spec.get("variables") or []}
    required = {
        "FURA_CONTENT_REPOSITORY",
        "FURA_CONTENT_REFRESH_TOKEN",
        "FURA_SESSION_SECRET",
        "FURA_BASE_URL",
        "FURA_IMAGE_VERSION",
        "FURA_IMAGE_CHANNEL",
        "FURA_IMAGE_DIGEST",
    }
    missing = sorted(required - variables.keys())
    if missing:
        failures.append(f"missing template variables: {', '.join(missing)}")
    for secret in ("FURA_CONTENT_REFRESH_TOKEN", "FURA_SESSION_SECRET"):
        variable = variables.get(secret) or {}
        if not variable.get("secret") or "secret(" not in str(variable.get("default") or ""):
            failures.append(f"{secret} must be a generated secret")
    if (variables.get("FURA_IMAGE_CHANNEL") or {}).get("default") != "stable":
        failures.append("template image channel must default to stable")
    gates = set(spec.get("publication_gates") or [])
    for phrase in ("clean-account conformance passes", "live demo SLO check passes"):
        if phrase not in gates:
            failures.append(f"missing publication gate: {phrase}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=ROOT / "config" / "railway-template-spec.json")
    parser.add_argument("--require-digest", action="store_true")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    failures = validate_template_spec(spec, require_digest=args.require_digest)
    print(json.dumps({"ok": not failures, "failures": failures}, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
