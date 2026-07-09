"""Runtime identity for deployed Furatena builds."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from furatena.catalog.channel_manifest import catalog_fingerprint

_PACKAGES = ("bengal-chirp", "bengal-pounce")


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
        "git_sha": git_sha or "unknown",
        "packages": {distribution: _package_version(distribution) for distribution in _PACKAGES},
        "freeze_fingerprint": catalog_fingerprint(catalog),
    }
