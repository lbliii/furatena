# Reusable per-module coverage ratchet

Furatena separates coverage ownership from a misleading repository-wide
percentage. The pattern is intentionally portable to Bengal, Chirp, and other
large suites.

## Shape

1. Define a bounded list of owned production modules and representative tests.
2. Run branch coverage with an isolated data file and emit machine-readable JSON.
3. Compare each module with a committed floor in `config/core-coverage.json`.
4. Fail when a measured module falls below its floor.
5. Also fail when coverage improves but the committed floor is not raised.
6. Upload the JSON report so reviewers can inspect the evidence.

The implementation is `scripts/check_core_coverage.py`, the `CORE_COVERAGE_*`
Makefile variables, and `make ci-coverage`. Copy all three parts together;
copying only the percentage values loses the ownership and stale-baseline
checks that make the ratchet trustworthy.

## Adoption recipe

Start with the current measured branch coverage, not an aspirational number.
Document excluded modules, give each included module its own floor, and improve
the list in reviewed increments. New modules should enter with a measured
baseline in the same change that adds them to the lane.

Keep this lane distinct from fast unit tests. Furatena's release workflow also
independently proves that a release tag is an ancestor of `main` before any
build or publishing identity is granted; the stack hygiene baseline's
description of that pre-publish gate matches the current workflow.
