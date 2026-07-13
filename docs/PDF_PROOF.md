# Cross-head PDF proof

`make pdf-proof` builds the hosted stress page, prints it with Chromium, generates native
page, collection, and site PDFs from the same catalog, rasterizes every sheet, and writes
`pdf-proof/artifacts/report.json` plus a short `summary.md`.

The report records:

- PDF metadata, physical page counts, outlines, link annotations, and extracted text;
- tagged-PDF and structure-tree diagnostics;
- the source URL, page title, and canonical URL observed by the browser;
- public and protected content sentinels;
- per-page raster dimensions, visible-ink ratios, dark-pixel ratios, and edge contact;
- regressions for mixed themes, dark print backgrounds, low-ink text-bearing pages,
  clipped edges, and a mostly blank final page.

The dedicated `Cross-head PDF proof` workflow runs when any app, content, renderer, theme,
dependency, or proof-harness input changes and uploads every PDF, PNG, and report. After a
successful Pages deployment, the main deployment workflow prints and verifies the live
`/proof/pdf-stress/` page again.

## Native baseline

`config/pdf-proof-baseline.json` names the native structural gaps owned by issue #432:
tagging, outlines, and annotations. Those diagnostics are
visible as known gaps; any additional diagnostic still fails CI. Run the harness with
`--strict` to treat every known gap as blocking. Issue #432 should remove the baseline as
the semantic native renderer lands.

The browser head has no baseline exemptions: it must be tagged and outlined, retain link
annotations and the public sentinel, exclude the protected sentinel, and pass every raster
check.
