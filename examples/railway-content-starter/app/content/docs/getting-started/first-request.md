---
title: Make your first request
description: Verify an Acme API token with a read-only project request.
weight: 10
---

# Make your first request

Create a development token in the Acme console and keep it in your shell rather
than in source control:

```console
export ACME_API_TOKEN="replace-with-a-development-token"
curl --fail --silent --show-error \
  --header "Authorization: Bearer $ACME_API_TOKEN" \
  https://api.example.com/v1/projects
```

A successful response returns a `projects` collection. Use a development
workspace until your integration handles authentication failures and retries.
