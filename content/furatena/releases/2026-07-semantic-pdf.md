---
title: Semantic native PDF and conformance gate
description: Source-faithful PDF publication with structural and visual release evidence.
date: 2026-07-14
draft: false
weight: 10
lang: en
type: doc
category: releases
---

# Semantic native PDF and conformance gate

`fura pdf` now renders the catalog's ordered Patitas document structure instead of a
truncated text projection. Headings, emphasis, clean clickable links, lists, repeated table
headers, code, callouts, figures, and explicit fallbacks retain source order across page,
collection, and site output. PDFs include searchable text, canonical metadata, outlines,
page numbers, and Furatena's `semantic-marked-content-v1` structure contract.

Letter, A4, and grayscale profiles are available. CLI `page_count` and manifest
`physical_page_count` report actual sheets; CLI `node_count` and manifest
`source_node_count` report selected catalog nodes.

The cross-head release gate now retains PDFs, full-page rasters, and machine-readable
reports for four native scope/profile combinations and four browser-print profiles. It
checks metadata, text completeness, visibility canaries, structure, annotations, clean
destinations, print lifecycle cleanup, background/contrast/clipping/blank-tail metrics,
and a 30-second full-site native generation budget. The tagging policy is machine-verified
but is not represented as independent PDF/UA certification.
