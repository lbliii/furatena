# Cross-head PDF proof

`make pdf-proof` builds the hosted stress page, prints it with Chromium across Letter,
A4, background-on/off, and grayscale profiles, generates native page, collection, and
site PDFs from the same catalog (including Letter and A4 page profiles), rasterizes every
sheet, and writes
`pdf-proof/artifacts/report.json` plus a short `summary.md`.

The report records:

- PDF metadata, physical page counts, outlines, link annotations, and extracted text;
- tagged-PDF and structure-tree diagnostics;
- the source URL, page title, and canonical URL observed by the browser;
- public and protected content sentinels;
- exact sentinel counts, tracking-free annotation destinations, and browser print-state cleanup;
- per-page raster dimensions, visible-ink ratios, dark-pixel ratios, and edge contact;
- regressions for mixed themes, dark print backgrounds, low-ink text-bearing pages,
  clipped edges, and a mostly blank final page.

The dedicated `Cross-head PDF proof` workflow runs when app, content, source
adapter, renderer, template, theme, PDF command, dependency, or proof-harness
inputs change and uploads every PDF, PNG, and report. Unrelated catalog modules
do not start this cross-head job. After a successful Pages deployment, the main
deployment workflow prints and verifies the live `/proof/pdf-stress/` page
again.

## Native conformance policy

`config/pdf-proof-baseline.json` contains no native exemptions. Native output must include
marked-content tags and a structure tree, catalog and authored-heading outlines, real link
annotations, truthful physical-page counts, and the required `H1`, `H2`, `P`, `L`, `Table`,
and `Code` structure types. The policy is intentionally narrower than a PDF/UA claim: CI
verifies Furatena's semantic-marked-content-v1 contract, but does not represent the output
as independently certified PDF/UA.

The browser head has no baseline exemptions: it must be tagged and outlined, retain link
annotations and the public sentinel, exclude the protected sentinel, and pass every raster
check.

The native full-site generation leg has a 30-second budget. Generation and verification
durations are retained per artifact in `report.json`, keeping performance drift distinct
from the physical-page and source-node counts.
