#!/usr/bin/env python3
"""Validate built distributions without importing from the source checkout."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

RUNTIME_SUFFIXES = {".css", ".html", ".js", ".json", ".py", ".svg", ".woff2"}


def _runtime_paths(source_root: Path) -> set[str]:
    package_root = source_root / "src" / "furatena"
    paths = {
        path.relative_to(source_root / "src").as_posix()
        for path in package_root.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix in RUNTIME_SUFFIXES or path.name == "py.typed")
    }
    if not paths:
        raise RuntimeError(f"no runtime package files found below {package_root}")
    return paths


def _wheel_paths(path: Path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name
            for name in archive.namelist()
            if name.startswith("furatena/") and not name.endswith("/")
        }


def _sdist_paths(path: Path) -> set[str]:
    marker = "/src/furatena/"
    with tarfile.open(path, mode="r:gz") as archive:
        paths = set()
        for member in archive.getmembers():
            if not member.isfile() or marker not in member.name:
                continue
            relative = member.name.split(marker, maxsplit=1)[1]
            paths.add(f"furatena/{relative}")
        return paths


def _assert_archive_complete(path: Path, expected: set[str]) -> None:
    actual = _wheel_paths(path) if path.suffix == ".whl" else _sdist_paths(path)
    missing = sorted(expected - actual)
    if missing:
        formatted = "\n  ".join(missing)
        raise RuntimeError(f"{path.name} is missing runtime package files:\n  {formatted}")


def _environment_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _environment_script(environment: Path, name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return environment / ("Scripts" if os.name == "nt" else "bin") / f"{name}{suffix}"


def _run(
    command: list[str], *, cwd: Path, env: dict[str, str], capture: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def _smoke_code(expected: set[str]) -> str:
    expected_json = json.dumps(sorted(expected))
    return f"""
import importlib.metadata
import importlib.resources
import json
import os
import pathlib
import sys

repo = pathlib.Path(os.environ["FURA_REPO_ROOT"]).resolve()
contaminated = []
for entry in sys.path:
    if not entry:
        continue
    resolved = pathlib.Path(entry).resolve()
    if resolved == repo or repo in resolved.parents:
        contaminated.append(str(resolved))
assert not contaminated, f"repository paths leaked into sys.path: {{contaminated}}"
assert hasattr(sys, "_is_gil_enabled"), "distribution smoke requires CPython 3.14t"
assert not sys._is_gil_enabled(), "distribution smoke unexpectedly enabled the GIL"

import furatena
from furatena.cli.main import main

package_file = pathlib.Path(furatena.__file__).resolve()
assert repo not in package_file.parents, f"furatena imported from checkout: {{package_file}}"
assert callable(main)
assert importlib.metadata.version("furatena")

root = importlib.resources.files("furatena")
missing = []
for relative in json.loads({expected_json!r}):
    candidate = root.joinpath(*pathlib.PurePosixPath(relative).parts[1:])
    if not candidate.is_file():
        missing.append(relative)
assert not missing, f"installed distribution is missing package data: {{missing}}"

scripts = {{entry.name: entry.value for entry in importlib.metadata.entry_points(group="console_scripts")}}
themes = {{entry.name: entry.value for entry in importlib.metadata.entry_points(group="furatena.themes")}}
assert scripts.get("fura") == "furatena.cli.main:main"
assert themes.get("lagoon") == "furatena.themes.lagoon:PACK"
"""


def _check_isolated_install(
    artifact: Path, *, expected: set[str], repo_root: Path, uv: str
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"furatena-{artifact.suffix.removeprefix('.')}-") as raw:
        workspace = Path(raw)
        environment = workspace / "venv"
        child_env = os.environ.copy()
        child_env.pop("PYTHONPATH", None)
        child_env["PYTHON_GIL"] = "0"
        child_env["FURA_REPO_ROOT"] = str(repo_root)

        _run(
            [uv, "venv", "--no-project", "--python", sys.executable, str(environment)],
            cwd=workspace,
            env=child_env,
        )
        python = _environment_python(environment)
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--strict",
                "--link-mode",
                "copy",
                "--no-config",
                str(artifact),
            ],
            cwd=workspace,
            env=child_env,
        )
        _run(
            [str(python), "-I", "-c", _smoke_code(expected)],
            cwd=workspace,
            env=child_env,
        )
        help_result = _run(
            [str(_environment_script(environment, "fura")), "--help"],
            cwd=workspace,
            env=child_env,
            capture=True,
        )
        if "usage:" not in (help_result.stdout or "").lower():
            raise RuntimeError(f"{artifact.name} CLI help did not contain a usage banner")
        print(f"validated isolated GIL-off install: {artifact.name}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    dist_dir = args.dist_dir.resolve()
    wheels = sorted(dist_dir.glob("furatena-*.whl"))
    sdists = sorted(dist_dir.glob("furatena-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise RuntimeError(
            f"expected exactly one furatena wheel and sdist in {dist_dir}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )

    expected = _runtime_paths(source_root)
    artifacts = [wheels[0], sdists[0]]
    for artifact in artifacts:
        _assert_archive_complete(artifact, expected)

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to create isolated distribution environments")
    for artifact in artifacts:
        _check_isolated_install(artifact, expected=expected, repo_root=source_root, uv=uv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
