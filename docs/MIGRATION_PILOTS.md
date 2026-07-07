# Representative migration pilots

Pilot snapshot: **2026-07-07**. Machine-readable evidence lives in
[`migration-pilots-v1.json`](migration-pilots-v1.json).

These read-only pilots exercised two public repositories at fixed revisions under
CPython 3.14t with `PYTHON_GIL=0`. The published evidence contains aggregate counts and
source-relative construct names only; it contains no copied source content, local paths,
host identity, or actor identity.

## Method

Each repository was shallow-cloned, mounted without modifying its sources, and scanned
with `fura migrate --report --json`. Activation is defined here as the wall-clock time
from invoking the readiness report on an initialized pilot app to receiving its complete
structured result. It is an automated tool-activation measurement, not a claim about a
human's clone-to-first-edit time.

```bash
PYTHON_GIL=0 fura --app-root PILOT_APP migrate --report --json
PYTHON_GIL=0 fura --app-root PILOT_APP migrate --apply-safe --dry-run --json
```

| Corpus | Revision | Sources | Report | Blocking sources | Clean-page rate |
| --- | --- | ---: | ---: | ---: | ---: |
| Public MDX vendor docs | `919e1793` | 953 | 13.53 s | 80 | 873/953 (91.6%) |
| Public Sphinx/MyST parser docs | `aa273e3f` | 28 | 1.27 s | 3 | 25/28 (89.3%) |
| Combined | — | 981 | — | 83 | 898/981 (91.5%) |

The combined first-pass result clears the 90% clean-page readiness target. “Clean” means
that a source has no blocking migration or content error; warnings still require review.
Every remaining finding is grouped by source and an owner field, which defaults to
`unassigned` when the upstream content has no Furatena owner metadata.

## MDX findings

The MDX corpus contained 931 `.mdx` files. Safe dry-run conversion classified 302 files
(32.4%) as reversible conversion candidates and 629 (67.6%) as manual. The largest
unsupported component families were `ResponseField`, `Note`, `Card`, `Update`, `Frame`,
`Badge`, `Tip`, `Steps`, and `CodeGroup`. Internal-link validation produced 244 blocking
findings across 80 sources.

Recommended migration order:

1. Add directive mappings for the repeated presentation primitives.
2. Apply and review the 302 source-preserving safe conversions.
3. Repair internal links before changing navigation.
4. Assign the remaining API-field and custom-component sources to content owners.

## Sphinx and MyST findings

The Sphinx/MyST corpus contained 28 tracked Markdown and RST sources. Its dominant manual
work is extension mapping: `myst-example`, Sphinx version directives, tabs, includes,
list tables, and project roles. Four blocking findings affected three sources: two nested
card-contract violations and two unresolved `{doc}` targets.

RST ingestion requires the optional format dependency:

```bash
pip install 'furatena[formats]'
```

The pilot validated that current docutils 0.23 parses, renders, and contributes headings
and links under Python 3.14t after the adapter compatibility repairs in this change.

## Product gaps converted to regression coverage

The pilots exposed three reproducible product defects, all covered by tests:

- Absolute mount roots were not canonicalized, so filesystem aliases such as macOS
  `/tmp` and `/private/tmp` could break source-relative path calculation.
- A migration report could claim zero findings when a catalog shard failed to load.
- RST parsing did not initialize current docutils settings and read reference attributes
  through an obsolete access pattern.

The remaining unsupported constructs are corpus-specific migration work, not silent
ingestion failures. They stay visible as structured remediation findings with source,
severity, construct, owner, and next action.
