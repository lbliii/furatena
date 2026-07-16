"""Runtime identity for deployed Furatena builds."""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from furatena.catalog.channel_manifest import catalog_fingerprint

_PACKAGES = ("bengal-chirp", "bengal-pounce")


def _content_identity() -> dict[str, Any]:
    state_root = os.environ.get("FURA_CONTENT_STATE_ROOT", "/data/furatena").strip()
    receipt = Path(state_root).expanduser() / "active" / "receipt.json"
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {"status": "embedded", "generation": None, "resolved_ref": None}
    if not isinstance(payload, dict):
        return {"status": "unknown", "generation": None, "resolved_ref": None}
    return {
        "status": str(payload.get("status") or "unknown"),
        "generation": payload.get("generation"),
        "resolved_ref": payload.get("resolved_ref"),
        "promoted_at": payload.get("promoted_at"),
    }


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "unknown"


def deployed_build_identity(catalog: Any) -> dict[str, Any]:
    """Describe the code, server stack, and frozen graph serving this request."""
    git_sha = (
        os.environ.get("FURA_BUILD_GIT_SHA")
        or os.environ.get("RAILWAY_GIT_COMMIT_SHA")
        or "unknown"
    ).strip()
    return {
        "content": _content_identity(),
        "distribution": os.environ.get("FURA_DISTRIBUTION", "source").strip() or "source",
        "git_sha": git_sha or "unknown",
        "image": {
            "channel": os.environ.get("FURA_IMAGE_CHANNEL", "development").strip() or "development",
            "digest": os.environ.get("FURA_IMAGE_DIGEST", "unknown").strip() or "unknown",
            "version": os.environ.get("FURA_IMAGE_VERSION", "development").strip() or "development",
        },
        "packages": {distribution: _package_version(distribution) for distribution in _PACKAGES},
        "freeze_fingerprint": catalog_fingerprint(catalog),
    }
