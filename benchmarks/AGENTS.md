<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: benchmarks

Keep catalog, author-runtime, retrieval, and score baselines reproducible, versioned, and sensitive to meaningful regressions.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Catalog, author-runtime, and retrieval benchmark harnesses return reproducible report shapes. | P1 | machine-backed | `uv run pytest tests/test_benchmark_harness.py tests/test_author_benchmark_harness.py tests/test_retrieval_benchmarks.py -q` (`benchmark-suite`) |

## Guardrails

- Benchmark changes state environment, corpus, warmup, sample count, metric units, and comparison baseline.
- Threshold or baseline updates require measured evidence and must not hide correctness failures.

## Edges

- run-by → **scripts** (benchmark harnesses)
- consumes → **evals** (retrieval datasets)

## Owns

- **code:** `benchmarks/`
- **tests:** `tests/test_benchmark_harness.py`, `tests/test_author_benchmark_harness.py`, `tests/test_retrieval_benchmarks.py`
- **docs:** `docs/PERFORMANCE.md`, `docs/AGENT_SCORE.md`
