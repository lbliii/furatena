# Issue 222 profile: index and search hot paths

Profile command, run on CPython 3.14.2 free-threading with `PYTHON_GIL=0`:

```bash
python -m cProfile -o /tmp/catalog.prof scripts/benchmark_catalog.py \
  --synthetic-pages 1000 --repeats 1 --query-iterations 50
```

Both profiles used the same 50-page dogfood and 1,000-page synthetic corpora.

| Synthetic operation | Before | After | Change |
| --- | ---: | ---: | ---: |
| Full index | 2,030.83 ms | 1,542.67 ms | -24.0% |
| Incremental reindex | 2,343.72 ms | 960.31 ms | -59.0% |
| Full freeze | 6,443.19 ms | 5,159.35 ms | -19.9% |
| Search (per query) | 44.43 ms | 9.19 ms | -79.3% |

The before profile made 10,504 YAML loads for 5,250 source parses because explicit
frontmatter was validated and then parsed again. The after profile makes 5,250 YAML
loads. Search previously performed 52,326 snippet builds and 54,264 plain-text
extractions across the profile. Ranking candidates now use the already-indexed
`DocNode.body_text`, and snippets are built only after sorting and limiting; the after
profile performs 1,224 snippet builds and 204 fallback plain-text extractions.

Ranking keys, scores, and snippet construction are unchanged. Regression tests assert
that snippet work is bounded by the requested result limit, indexed body text is reused,
and explicit frontmatter performs one YAML traversal while preserving numeric metadata
normalization.
