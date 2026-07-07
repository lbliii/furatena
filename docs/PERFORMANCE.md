# Performance benchmarks

Run the repeatable catalog benchmark with free-threaded Python:

```bash
make benchmark
```

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
