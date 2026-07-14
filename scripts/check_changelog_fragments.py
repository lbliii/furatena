#!/usr/bin/env python3
"""Validate Towncrier fragments and require release-note intent on pull requests."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT_DIR = ROOT / "changelog.d"
FRAGMENT_TYPES = frozenset({"added", "changed", "deprecated", "removed", "fixed", "security"})
FRAGMENT_RE = re.compile(
    rf"^(?P<issue>[1-9]\d*)\.(?P<kind>{'|'.join(sorted(FRAGMENT_TYPES))})\.md$"
)


def validate_fragments(fragment_dir: Path = FRAGMENT_DIR) -> tuple[str, ...]:
    """Return deterministic naming and content findings for committed fragments."""
    findings: list[str] = []
    if not fragment_dir.is_dir():
        return (f"missing changelog fragment directory: {fragment_dir}",)
    for path in sorted(fragment_dir.iterdir(), key=lambda item: item.name):
        if path.name in {"AGENTS.md", "README.md"}:
            continue
        if not path.is_file():
            findings.append(f"changelog fragment entries must be files: {path}")
            continue
        if FRAGMENT_RE.fullmatch(path.name) is None:
            findings.append(
                f"invalid changelog fragment name {path.name!r}; expected ISSUE.TYPE.md"
            )
            continue
        if not path.read_text(encoding="utf-8").strip():
            findings.append(f"changelog fragment is empty: {path.name}")
    return tuple(findings)


def changed_paths(base_ref: str, *, root: Path = ROOT) -> tuple[str, ...]:
    """Return paths changed from one merge base through HEAD."""
    completed = subprocess.run(
        ("git", "diff", "--name-only", "--diff-filter=ACMR", f"{base_ref}...HEAD"),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "git diff failed"
        raise RuntimeError(f"could not compare changelog intent against {base_ref}: {message}")
    return tuple(line.strip() for line in completed.stdout.splitlines() if line.strip())


def has_release_note_intent(paths: tuple[str, ...]) -> bool:
    """Return whether a change carries a fragment or an assembled changelog."""
    return "CHANGELOG.md" in paths or any(
        path.startswith("changelog.d/") and FRAGMENT_RE.fullmatch(Path(path).name) for path in paths
    )


def _default_base_ref() -> str:
    explicit = os.environ.get("CHANGELOG_BASE_REF", "").strip()
    if explicit:
        return explicit
    github_base = os.environ.get("GITHUB_BASE_REF", "").strip()
    return f"origin/{github_base}" if github_base else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default=_default_base_ref())
    args = parser.parse_args(argv)

    findings = list(validate_fragments())
    if args.base_ref:
        try:
            paths = changed_paths(args.base_ref)
        except RuntimeError as exc:
            findings.append(str(exc))
        else:
            if paths and not has_release_note_intent(paths):
                findings.append(
                    "pull-request changes require changelog.d/ISSUE.TYPE.md or an assembled "
                    "CHANGELOG.md update"
                )
    if findings:
        print("Changelog hygiene failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print("Changelog hygiene clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
