# PyPI publishing

Furatena publishes the open-source `furatena` package to PyPI with **Trusted
Publishing** (OIDC). No long-lived PyPI API token is stored in the repository.
The workflow is `.github/workflows/python-publish.yml`, matching the chirp,
kida, murlocs, and milo-cli pattern: gate → build → OIDC publish on a published
GitHub Release.

The PyPI project name `furatena` was unclaimed as of 2026-08-05.

## One-time setup (before first release)

1. **Make the GitHub repository public** (`lbliii/furatena`). Trusted Publishing
   and public install docs assume a public source repository.
2. Create a GitHub Environment named **`pypi`**
   (Settings → Environments → New environment).
   Optionally require reviewers and restrict deployment branches/tags.
3. On [PyPI Trusted Publishers](https://pypi.org/manage/account/publishing/),
   register a pending publisher (or project publisher after the first upload)
   with these exact values:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `furatena` |
   | Owner | `lbliii` |
   | Repository | `furatena` |
   | Workflow name | `python-publish.yml` |
   | Environment name | `pypi` |

4. Confirm package metadata locally:

   ```bash
   export PYTHON_GIL=0
   uv sync --group dev
   make ci-release
   uv build --clear --no-sources
   uvx twine check dist/*
   ```

## Every release

1. Ensure `pyproject.toml` `[project].version` and
   `src/furatena/__init__.py` `__version__` are identical.
2. Assemble notes and land them on `main`:

   ```bash
   VERSION=0.1.2 make changelog   # or omit VERSION to use pyproject
   git add CHANGELOG.md changelog.d && git commit -m "Release notes for 0.1.2"
   git push origin main
   ```

3. Create the GitHub Release (triggers `.github/workflows/python-publish.yml`):

   ```bash
   make gh-release
   ```

   This tags `v<version>` at `origin/main`, publishes the release from the
   matching `CHANGELOG.md` section, and kicks Trusted Publishing — same pattern
   as chirp/kida/milo-cli (`make gh-release`).
4. Confirm the Actions run **Upload Python Package** succeeds and
   https://pypi.org/p/furatena shows the new version.
5. Smoke the published package in a clean environment:

   ```bash
   uvx --from furatena==<version> fura --help
   ```

## Install after publish

```bash
pip install furatena
# or
uv tool install furatena
fura --help
fura init ./my-docs --name "My Docs"
fura --app-root ./my-docs serve
```

Requires free-threaded CPython 3.14 (`3.14t`) with `PYTHON_GIL=0` for the
supported runtime line. The publish job itself does **not** set
`PYTHON_GIL=0` — the PyPI upload action runs a non-free-threaded interpreter.

## Non-goals

- This runbook does not replace GHCR private-image operations; those remain
  optional and are documented in [RELEASING.md](RELEASING.md).
- Railway template marketplace listing and kickbacks are separate operator
  work; the template should install Furatena from PyPI once this path is live.
