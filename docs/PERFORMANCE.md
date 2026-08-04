# Performance benchmarks

Run the repeatable catalog benchmark with free-threaded Python:

```bash
make benchmark
```

Benchmark retrieval quality and cost against the versioned known-answer corpus:

```bash
make retrieval-benchmark
make retrieval-benchmark BENCHMARK_ARGS="--repeats 5 --limit 12 --output benchmarks/retrieval-baseline.json"
```

The retrieval report compares keyword-only ranking, TF-IDF-only ranking,
additive hybrid fusion, and keyword-guarded hybrid reranking. Each mode reports
Recall@3, MRR, no-result rate, median query latency, estimated index memory, and
serialized index size. The same run verifies mount, edition, tag, URL-prefix,
and public/private access filters for every algorithm.

`reranked_hybrid` is the default. It preserves deterministic keyword ordering
when lexical evidence exists, then uses semantic matches as fallback. Tuning
controls are the result `limit`, semantic candidate limit (`max(limit * 3,
64)`), ranking mode (`keyword_guarded` or `additive`), and the five filters
listed above. The benchmark rejects candidate rerankers that reduce measured
quality even when they are technically valid.

The committed `benchmarks/retrieval-baseline.json` records the selected
free-threaded baseline and every quality, cost, and filter result. Latency is
informational across machines; quality and filter outcomes are deterministic
contracts covered in fast CI.

The command measures full index, body-only incremental reindex, full freeze,
`/catalog/query.json`-equivalent graph queries, and ranked search against two isolated
corpora: a copy of the Furatena dogfood documentation and a deterministic synthetic
corpus. Cold operations construct fresh state; warm query/search operations perform one
warmup and report per-operation time. Each result includes every sample and its median.

Tune the corpus and sample counts without editing the harness:

```bash
make benchmark BENCHMARK_ARGS="--synthetic-pages 1000 --repeats 5 --query-iterations 25"
```

The harness refuses a GIL-enabled interpreter. `make benchmark` sets `PYTHON_GIL=0`
through the shared `UV_RUN` command, matching CI and production validation. Results are
informational rather than pass/fail thresholds because host hardware and load materially
affect wall-clock time. The committed `benchmarks/catalog-baseline.json` captures the
initial methodology, environment, samples, and medians; regenerate it after intentional
performance work:

```bash
make benchmark BENCHMARK_ARGS="--output benchmarks/catalog-baseline.json"
```

## Author-runtime profile

Measure the author-mode paths separately from catalog indexing:

```bash
make author-benchmark
make author-benchmark BENCHMARK_ARGS="--synthetic-pages 25 --repeats 5 --output benchmarks/profiles/author-runtime.json"
```

The default profile uses an isolated deterministic corpus. Add `--dogfood` to use the
repository's configured mounts. The versioned v1 report independently measures author
`DocsApp` construction, cold and warm composed serve preflight (freeze plus the one Chirp
contract pass), the first and warm full-page request, page status JSON, explicit
validation, and the full docs validation pipeline. It also records corpus size,
dependency versions, free-threading state, and per-phase profiler timings. The
`startup_contract_checks` operation uses the same typed preflight service as `fura serve`,
with Pounce networking and terminal rendering excluded from the timing.

Every operation includes structural call counts for secondary `DocsApp` construction and
full `check_catalog()` execution. These counts are stable CI signals; wall-clock samples
remain informational across machines. The command requires free-threaded CPython with
`PYTHON_GIL=0`, like the catalog and retrieval benchmarks. The report contract is
`author-runtime-benchmark-v1.schema.json` in the packaged catalog schemas.

`benchmarks/profiles/issue-373-before.json` and `issue-374-after.json` are the matched
pre/post snapshot profiles. Their methodology, medians, and proposed relative diagnostic
budgets are recorded in `benchmarks/profiles/issue-373-author-runtime.md`. Fast CI guards
the portable structural contract: unchanged ordinary page/status requests perform zero
full validation runs and construct zero secondary `DocsApp` instances.

`benchmarks/profiles/issue-486-after.json` records the phase-aware serve-preflight path
with a 12-page synthetic corpus, one sample, one worker, and no autodoc. The cold
preflight performs one full validation and zero secondary `DocsApp` constructions; the
warm preflight performs zero full validations and zero secondary constructions. Its
wall-clock samples are environment evidence, not a threshold or a direct comparison to
the earlier three-repeat profile.

## Optimization profiles

Profile evidence and methodology for applied optimizations live under
`benchmarks/profiles/`. The issue 222 profile removes duplicate frontmatter parsing and
defers search snippet extraction until after ranking/limit selection. On the 1,000-page
synthetic profile this reduced search time by 79%, full-index time by 24%, and total
function calls from 75.0 million to 54.2 million without changing ranking or output
contracts.

Incremental reindexing records its last computed regions per slug through
`DocCatalog.invalidation_regions_for()`. Fast CI instruments graph finalization and fails
if a body-only edit triggers backlink/edge recomputation; separate metadata and link-edit
cases assert their exact region and rebuild behavior.

## Frozen bulk artifacts

Preview and hybrid servers return the frozen bytes for `/catalog.json`, the unfiltered
`/search.json`, `/semantic.json`, `/structure.json`, `/tools.json`,
`/catalog/api-operations.json`, `/llms.txt`, and `/llms-full.txt`. Freeze writes the JSON
sidecars compactly, and the runtime reads the files without rebuilding page records,
structure indexes, or serialized payloads. A filtered `/search.json?q=...` remains a
live query over the loaded frozen index.

Frozen responses include a SHA-256 `ETag`, file `Last-Modified`, and
`Cache-Control: public, max-age=0, must-revalidate`. Conditional requests are answered
with `304 Not Modified`. Author mode keeps live serialization because its graph is
mutable and may include authorized private content.
