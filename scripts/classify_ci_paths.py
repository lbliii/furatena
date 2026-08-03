#!/usr/bin/env python3
"""Classify changed paths into the expensive pull-request CI lanes they require."""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

LANE_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "coverage": (
        ".github/workflows/pages.yml",
        "Makefile",
        "config/core-coverage.json",
        "pyproject.toml",
        "scripts/check_core_coverage.py",
        "scripts/classify_ci_paths.py",
        "src/**",
        "tests/**",
        "uv.lock",
    ),
    "browser": (
        ".github/workflows/pages.yml",
        "Makefile",
        "app/**",
        "content/**",
        "pyproject.toml",
        "scripts/classify_ci_paths.py",
        "scripts/pages-build.sh",
        "src/furatena/catalog/_templates/**",
        "src/furatena/catalog/directives/**",
        "src/furatena/catalog/docs_app.py",
        "src/furatena/catalog/render*.py",
        "src/furatena/catalog/static_export.py",
        "src/furatena/catalog/template_env.py",
        "src/furatena/catalog/theme*.py",
        "src/furatena/themes/**",
        "tests/test_browser*.py",
        "uv.lock",
    ),
    "release": (
        ".github/workflows/pages.yml",
        "Makefile",
        "README.md",
        "pyproject.toml",
        "scripts/check_distributions.py",
        "scripts/classify_ci_paths.py",
        "src/**",
        "tests/test_catalog_packaging.py",
        "tests/test_release_publishing.py",
        "uv.lock",
    ),
}


def classify_paths(paths: Iterable[str], *, force_all: bool = False) -> dict[str, bool]:
    """Return whether each expensive lane is required for the changed paths."""

    normalized = tuple(path.strip().removeprefix("./") for path in paths if path.strip())
    return {
        lane: force_all
        or any(fnmatch.fnmatchcase(path, pattern) for path in normalized for pattern in patterns)
        for lane, patterns in LANE_PATTERNS.items()
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-all", action="store_true")
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = () if args.force_all else sys.stdin
    classification = classify_paths(paths, force_all=args.force_all)
    lines = [
        f"{lane}-required={'true' if required else 'false'}"
        for lane, required in classification.items()
    ]
    payload = "\n".join(lines) + "\n"
    if args.github_output is None:
        sys.stdout.write(payload)
    else:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
