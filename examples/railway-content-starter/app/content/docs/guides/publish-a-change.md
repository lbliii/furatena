---
title: Publish a documentation change
description: Review and publish an adopter-owned content generation.
weight: 10
---

# Publish a documentation change

1. Create a branch and edit Markdown under `app/content/`.
2. Open a pull request and review links, navigation, and public-safe content.
3. Merge to `main` after approval.
4. Let the repository workflow request an authenticated content refresh.
5. Confirm the resolved commit and active generation in `/meta.json`.
6. Verify that the page appears in reader, search, catalog, and agent outputs.

If validation fails, the active generation remains unchanged. Correct the
reported source problem and retry rather than rebuilding the Furatena image.
