# OpenAPI, DevRel, and multi-mount platform pilots

Pilot snapshot: **2026-07-07**. Machine-readable evidence lives in
[`platform-pilots-v1.json`](platform-pilots-v1.json).

The pilots used fixed revisions of public repositories under CPython 3.14t with
`PYTHON_GIL=0`. Published results contain aggregate counts and public revisions, not
copied source content, local paths, host identity, or actor identity.

## OpenAPI and DevRel

The API pilot used GitHub's public REST API description at revision `dd9fab13`. The
source contains 1,194 operations across 789 paths. Furatena generated and froze 1,198
pages, then exported 1,212 static pages and 1,213 sidecars with a `/platform`-style base
path.

| Contract | Result |
| --- | ---: |
| API operations in frozen and static agent inventory | 1,194 |
| Operation tag groups | 47 |
| Operations with examples | 959 |
| Operations with schemas | 858 |
| Static operations permitting live requests | 0 |
| Content/governance check | 7.44 s |
| Freeze | 18.90 s |
| Static export | 50.28 s |

Governance found nine invalid, summary-only OpenAPI Example Objects. Every operation had
an operation ID and summary; the errors are confined to those upstream example records.
The approval decision is explicit: repair the examples or record a reviewed compatibility
decision before making warnings-as-errors a release gate.

The release workflow compared the repository's `2022-11-28` and `2026-03-10` API
descriptions in 14.44 seconds. It reported 10 changed operations, including four breaking
changes, with no additions or removals. That result is suitable for release-note and
compatibility review input.

The frozen-to-static round trip initially dropped `api_operation` and `api_try_it`
metadata, leaving the agent inventory empty even though the reference pages existed. The
loader now restores both records, and a regression proves that the frozen catalog, tools
manifest, `llms.txt`, and `/catalog/api-operations.json` retain the same operation set.

## Git source and multi-mount federation

The federation pilot mounted Flask and Werkzeug documentation directly from Git sources,
plus a small local product mount. Furatena resolved each repository to an immutable
commit, indexed 114 public remote pages, validated two cross-mount links, froze all three
mounts, and exported the combined static site.

| Mount | Revision | Public pages |
| --- | --- | ---: |
| Flask | `36e4a824` | 76 |
| Werkzeug | `1b00618e` | 38 |
| Local product integration | local | 3 |

The final content check had zero errors and 1,633 actionable warnings. Those warnings are
the expected Sphinx extension surface—roles, autodoc directives, version directives, and
renderer options that need mapping or source decisions before conversion. They do not
hide source-sync or catalog failures.

Freeze indexed all mounts in 7.001 seconds and rendered them in 3.244 seconds. Static
export completed in 16.6 seconds with 131 pages and 132 sidecars. Both cross-mount links
were rebased correctly under the deployment base path.

A private sentinel page was present in the author source. Freeze recorded one visibility
canary, and the sentinel appeared in zero public HTML, text, catalog, search, tools, or
agent artifacts.

Real RST content also exposed missing line numbers on some docutils reference nodes. DCP
v3 requires positive locations for Content IR links, headings, and directives. The RST
adapter now inherits the nearest source line and falls back to line 1, with a regression
covering the exported link contract.

## Deployment decision

Both pilots select the `static-pages` deployment profile. Remaining approval blockers are
named above: upstream OpenAPI example validity, Sphinx extension mapping ownership, and
installation of `furatena[formats]` for RST mounts. No private-content or authenticated
try-it boundary remains implicit.
