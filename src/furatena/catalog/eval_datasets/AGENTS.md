<!-- generated from .stewards/manifest.toml — edit the manifest, not this file -->

# Steward: evals

Keep retrieval and agent evaluation datasets versioned, deterministic, representative, and resistant to threshold gaming.

Ordinary work: use this map directly with the root map and run only affected checks.
Do not open `.stewards/PROTOCOL.md` or `.stewards/manifest.toml` unless the task is an explicit review/audit or steward-network maintenance.

## Protects

| Invariant | Sev | Backing | Proof / anchor |
| --- | --- | --- | --- |
| Versioned retrieval datasets, known answers, thresholds, and score-lane behavior remain executable. | P1 | machine-backed | `uv run pytest tests/test_retrieval_dataset.py tests/test_retrieval_conformance.py tests/test_afdocs_score_lane.py -q` (`eval-suite`) |

## Guardrails

- Known answers, thresholds, scoring semantics, dataset version, and benchmark reports change together.
- Threshold changes require measured evidence; do not weaken a gate to bless a regression.

## Edges

- measured-by → **benchmarks** (retrieval benchmark)
- verifies → **catalog** (search and agent retrieval)

## Owns

- **code:** `src/furatena/catalog/eval_datasets/`
- **tests:** `tests/test_retrieval_dataset.py`, `tests/test_retrieval_conformance.py`, `tests/test_afdocs_score_lane.py`
- **docs:** `docs/AGENT_SCORE.md`
