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
