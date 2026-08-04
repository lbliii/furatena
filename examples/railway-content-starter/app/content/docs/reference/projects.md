---
title: Projects API
description: List and inspect the projects visible to the current token.
weight: 10
layout: api_reference
---

# Projects API

## List projects

`GET /v1/projects`

Returns projects visible to the current token. The response contains a
`projects` array whose entries include immutable `id`, mutable `name`, and
`environment` fields.

## Get a project

`GET /v1/projects/{project_id}`

Returns one project or `404` when the project does not exist or is outside the
token's scope. Do not infer project existence from an authorization failure.
