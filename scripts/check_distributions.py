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
import tomllib
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


def _project_version(source_root: Path) -> str:
    with (source_root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def _smoke_code(expected: set[str], expected_version: str) -> str:
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
assert importlib.metadata.version("furatena") == {expected_version!r}

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


def _live_smoke_code() -> str:
    return """
import asyncio
import os
import pathlib

from furatena.catalog.docs_app import DocsApp
from furatena.catalog.runtime import ServeConfig, ServeMode

app_root = pathlib.Path(os.environ["FURA_SMOKE_APP"])
docs = DocsApp.from_paths(
    app_root / "docs.yaml",
    repo_root=app_root,
    autodoc=False,
    serve=ServeConfig(ServeMode.AUTHOR, None, False, False),
)
assert docs.catalog.get_path("/docs/get-started/") is not None
app = docs.create_app()

async def fetch():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/docs/get-started/",
        "raw_path": b"/docs/get-started/",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 0),
    }
    body_sent = False
    status = 500
    body = []

    async def receive():
        nonlocal body_sent
        if not body_sent:
            body_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    await app(scope, receive, send)
    assert status == 200
    assert "Get started" in b"".join(body).decode("utf-8")

asyncio.run(fetch())
"""


def _assert_workflow_outputs(app_root: Path) -> None:
    required = {
        "frozen/catalog.json",
        "frozen/search.json",
        "frozen/semantic.json",
        "frozen/structure.json",
        "frozen/tools.json",
        "frozen/channels.json",
        "public/docs/get-started/index.html",
        "public/catalog.json",
        "public/search.json",
        "public/semantic.json",
        "public/tools.json",
        "public/llms.txt",
        "public/llms-full.txt",
        "public/channels.json",
        "public/docs-assets/manifest.json",
        "public/docs-vendor/htmx.min.js",
    }
    missing = sorted(relative for relative in required if not (app_root / relative).is_file())
    if missing:
        raise RuntimeError(f"packaged workflow did not generate required outputs: {missing}")
    if not list((app_root / "public" / "docs-assets").glob("theme.*.css")):
        raise RuntimeError("packaged workflow did not generate the hashed theme stylesheet")
    if not list((app_root / "public" / "docs-theme" / "fonts").glob("*.woff2")):
        raise RuntimeError("packaged workflow did not copy theme fonts")

    page = (app_root / "public" / "docs" / "get-started" / "index.html").read_text(
        encoding="utf-8"
    )
    if "Get started" not in page:
        raise RuntimeError("packaged workflow generated an unexpected get-started page")
    for root in (app_root / "frozen", app_root / "public"):
        channels = json.loads((root / "channels.json").read_text(encoding="utf-8"))
        ids = {channel["id"] for channel in channels["channels"]}
        if not {"agent", "pdf"} <= ids:
            raise RuntimeError(f"{root / 'channels.json'} is missing agent output channels")


def _check_isolated_install(
    artifact: Path,
    *,
    expected: set[str],
    expected_version: str,
    repo_root: Path,
    uv: str,
) -> None:
    with tempfile.TemporaryDirectory(prefix=f"furatena-{artifact.suffix.removeprefix('.')}-") as raw:
        workspace = Path(raw)
        environment = workspace / "venv"
        child_env = os.environ.copy()
        child_env.pop("PYTHONPATH", None)
        child_env["PYTHON_GIL"] = "0"
        child_env["FURA_REPO_ROOT"] = str(repo_root)
        app_root = workspace / "standalone-app"
        child_env["FURA_SMOKE_APP"] = str(app_root)

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
            [str(python), "-I", "-c", _smoke_code(expected, expected_version)],
            cwd=workspace,
            env=child_env,
        )
        fura = str(_environment_script(environment, "fura"))
        help_result = _run(
            [fura, "--help"],
            cwd=workspace,
            env=child_env,
            capture=True,
        )
        if "usage:" not in (help_result.stdout or "").lower():
            raise RuntimeError(f"{artifact.name} CLI help did not contain a usage banner")
        _run(
            [fura, "init", str(app_root), "--name", "Packaged Smoke", "--json"],
            cwd=workspace,
            env=child_env,
        )
        _run(
            [
                fura,
                "--app-root",
                str(app_root),
                "check",
                "--content-only",
                "--warnings-as-errors",
                "--json",
            ],
            cwd=workspace,
            env=child_env,
        )
        _run(
            [str(python), "-I", "-c", _live_smoke_code()],
            cwd=workspace,
            env=child_env,
        )
        _run(
            [fura, "--app-root", str(app_root), "freeze", "--json"],
            cwd=workspace,
            env=child_env,
        )
        _run(
            [fura, "--app-root", str(app_root), "export", "--base-path", "", "--json"],
            cwd=workspace,
            env=child_env,
        )
        _assert_workflow_outputs(app_root)
        print(f"validated packaged standalone workflow: {artifact.name}")


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
    expected_version = _project_version(source_root)
    artifacts = [wheels[0], sdists[0]]
    normalized_version = expected_version.replace("-", "_")
    if any(not artifact.name.startswith(f"furatena-{normalized_version}") for artifact in artifacts):
        raise RuntimeError(
            f"distribution filenames do not match project version {expected_version}: "
            f"{[artifact.name for artifact in artifacts]}"
        )
    for artifact in artifacts:
        _assert_archive_complete(artifact, expected)

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to create isolated distribution environments")
    for artifact in artifacts:
        _check_isolated_install(
            artifact,
            expected=expected,
            expected_version=expected_version,
            repo_root=source_root,
            uv=uv,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
