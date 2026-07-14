---
title: Governed pull-request previews
description: Review the human site and agent surfaces from one protected, commit-bound build.
weight: 35
owner: docs-product
reviewed_at: "2026-07-14"
tags: [previews, railway, github, governance, agents]
---

# Governed pull-request previews

Furatena turns a documentation pull request into one review object: a protected
live site, negotiated Markdown, search, catalog queries, `llms.txt`, and build
metadata frozen from the same immutable commit.

The PR shows one lifecycle check and one durable comment. Reviewers see the
current head SHA, freeze identity, direct human and agent links, and concise
conformance results without navigating provider dashboards. A newer commit
supersedes the prior deployment; closing or merging removes the environment.

Security is the default rather than an adopter add-on. Preview routes require a
per-PR credential, prohibit indexing and shared caching, deny bot and untrusted-
fork deployment, and exclude mutable author surfaces and production credentials.
Provider-specific orchestration stays behind Furatena's versioned preview
manifest, with Railway as the maintained reference integration.

Start with the `governed-preview` repository starter and the adopter guide in
`docs/GOVERNED_PR_PREVIEWS.md`. The same conformance command works with another
provider when it emits the portable manifest contract.
