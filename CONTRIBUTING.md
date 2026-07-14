# Contributing to Furatena

Furatena targets free-threaded CPython 3.14 and keeps behavior aligned across
browser, static, CLI, and agent surfaces.

## Set up

```bash
uv sync --group dev
PYTHON_GIL=0 uv run fura check
```

Use a focused branch and preserve unrelated working-tree changes. Do not stage
or rewrite files outside the intended change.

## Validate

Run the smallest relevant tests while iterating, then the lanes affected by the
change:

```bash
make ci-fast
make ci-contract
make ci-coverage
make ci-agent
make ci-release
```

Browser, export, and PDF changes also require their corresponding `ci-browser`,
`ci-export`, or `ci-pdf-proof` lane. See [docs/CI.md](docs/CI.md).

## Describe the change

Add `changelog.d/ISSUE.TYPE.md` using one of `added`, `changed`, `deprecated`,
`removed`, `fixed`, or `security`. Write for users and operators, not the Git
history. Preview assembled notes with `make changelog-draft`.

Pull requests should state the outcome, validation evidence, compatibility or
security impact, and any follow-up that remains deliberately open.

## Releases

Maintainers prepare and review the assembled changelog before tagging. Tagged
commits must be ancestors of `main`, pass the release gates, and match package
metadata exactly. Follow [docs/RELEASING.md](docs/RELEASING.md); never move a
published tag or replace an uploaded distribution.
