#!/usr/bin/env python3
"""One-time migration helpers after copying sources from Chirp."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".docs-cache",
    ".docs-cache-test",
    "frozen",
    "public",
    ".preview",
    ".git",
    ".venv",
}

TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".html",
    ".js",
    ".css",
    ".json",
    ".sh",
    ".toml",
    ".txt",
}


def iter_files(base: Path):
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {
            "Makefile",
            "LICENSE",
            "run",
            "freeze",
            "export",
            "preview",
        }:
            yield path


def transform(text: str, *, path: Path) -> str:
    # Package imports
    text = re.sub(r"\bfrom catalog\b", "from furatena.catalog", text)
    text = re.sub(r"\bimport catalog\b", "import furatena.catalog as catalog", text)

    # Env vars (Fura CLI / runtime)
    text = text.replace("CHIRP_DOCS_", "FURA_")
    text = text.replace("window.FURA_STATIC", "window.FURA_STATIC")  # idempotent
    text = text.replace("chirp_docs_author", "fura_author")
    text = text.replace("chirp-docs-", "fura-")

    # Paths in this repo
    text = text.replace("examples/chirp_docs", "app")
    text = text.replace("../../site/content", "../../content/chirp")
    text = text.replace("../../site/data", "../../data")
    text = text.replace('repo / "site" / "content"', 'repo / "content" / "chirp"')
    text = text.replace(
        'repo / "site" / "config" / "_default" / "autodoc.yaml"', 'repo / "config" / "autodoc.yaml"'
    )
    text = text.replace("site/content", "content/chirp")
    text = text.replace("site/data", "data")

    if path.name == "mounts.yaml":
        text = text.replace("content_root: ../../content/chirp", "content_root: ../content/chirp")
        text = text.replace("content_root: content/shared", "content_root: content/shared")

    if path.name == "docs.yaml":
        text = text.replace("data: ../../data/collections.yaml", "data: ../data/collections.yaml")
        text = text.replace(
            "rewrites: ../../data/url_rewrites.yaml", "rewrites: ../data/url_rewrites.yaml"
        )

    # Branding (light touch)
    text = text.replace("Chirp Docs", "Furatena")
    text = text.replace("chirp docs", "fura")
    text = text.replace("``chirp docs``", "``fura``")

    return text


def main() -> int:
    changed = 0
    for path in iter_files(ROOT):
        if path == Path(__file__).resolve():
            continue
        original = path.read_text(encoding="utf-8")
        updated = transform(original, path=path)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    print(f"Updated {changed} files under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
