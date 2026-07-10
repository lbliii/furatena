#!/usr/bin/env python3
"""Validate a release tag and generate deterministic distribution integrity metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG_COMPONENT = r"(?:0|[1-9]\d*)"
TAG_PATTERN = re.compile(rf"^v(?P<version>{TAG_COMPONENT}\.{TAG_COMPONENT}\.{TAG_COMPONENT})$")


def project_version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def validate_release_context(tag: str, *, root: Path = ROOT) -> str:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"release tag must be an exact vMAJOR.MINOR.PATCH tag, got {tag!r}")
    version = project_version(root)
    if match.group("version") != version:
        raise ValueError(f"release tag {tag!r} does not match project version {version!r}")
    package_version = _package_version(root / "src" / "furatena" / "__init__.py")
    if package_version != version:
        raise ValueError(
            f"furatena.__version__ {package_version!r} does not match project version {version!r}"
        )
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 14):
        raise RuntimeError("release metadata requires the supported CPython 3.14 runtime")
    gil_probe = getattr(sys, "_is_gil_enabled", None)
    if not callable(gil_probe) or gil_probe() or os.environ.get("PYTHON_GIL") != "0":
        raise RuntimeError("release metadata requires CPython 3.14t with PYTHON_GIL=0")
    return version


def prepare_release(
    *,
    tag: str,
    commit: str,
    dist_dir: Path,
    output_dir: Path,
    root: Path = ROOT,
) -> dict[str, object]:
    version = validate_release_context(tag, root=root)
    artifacts = _distribution_artifacts(dist_dir, version)
    records = [
        {
            "filename": artifact.name,
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "size": artifact.stat().st_size,
        }
        for artifact in artifacts
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums = "".join(f"{record['sha256']}  {record['filename']}\n" for record in records)
    (output_dir / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "project": "furatena",
        "version": version,
        "tag": tag,
        "commit": commit,
        "runtime": {
            "implementation": "CPython",
            "version": "3.14",
            "abi": "free-threaded",
            "gil_enabled": False,
            "PYTHON_GIL": "0",
        },
        "artifacts": records,
    }
    (output_dir / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _package_version(path: Path) -> str:
    match = re.search(
        r'^__version__\s*=\s*["\'](?P<version>[^"\']+)["\']',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"could not find __version__ in {path}")
    return match.group("version")


def _distribution_artifacts(dist_dir: Path, version: str) -> tuple[Path, Path]:
    wheels = sorted(dist_dir.glob(f"furatena-{version}-*.whl"))
    sdists = sorted(dist_dir.glob(f"furatena-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            "release requires exactly one wheel and one sdist for the project version; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s) in {dist_dir}"
        )
    return wheels[0], sdists[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", default="")
    parser.add_argument("--dist-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if args.validate_only:
        version = validate_release_context(args.tag)
        print(json.dumps({"ok": True, "tag": args.tag, "version": version}, sort_keys=True))
        return
    if args.dist_dir is None or args.output_dir is None or not args.commit.strip():
        parser.error("--dist-dir, --output-dir, and --commit are required unless --validate-only")
    manifest = prepare_release(
        tag=args.tag,
        commit=args.commit.strip(),
        dist_dir=args.dist_dir,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
