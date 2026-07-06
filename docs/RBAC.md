# RBAC Model

Furatena uses one small role-based access model for browser routes, static and
agent exports, authoring, and administration. The model is intentionally
conservative: pages are public only when lifecycle visibility is public and no
extra access policy is declared.

The role model is applied to concrete browser, CLI, and MCP mutation boundaries
in [AUTHOR_MUTATION_THREAT_MODEL.md](AUTHOR_MUTATION_THREAT_MODEL.md).

## Roles

Roles are ordered. A higher role includes the lower-role capabilities.

| Role | Intended user | Baseline permissions |
|------|---------------|----------------------|
| `anonymous` | Public visitor or static crawler | `read`, `search`, `retrieve`, `export` for public pages only |
| `reader` | Signed-in viewer | Public plus private, internal, and unlisted read surfaces |
| `contributor` | Author | Reader plus `author`; can read draft pages |
| `publisher` | Release owner | Contributor plus `publish` |
| `admin` | Site operator | All permissions including `configure` and `administer` |

## Permissions

The built-in permission names are:

- `read`: render a page or mount surface.
- `search`: include content in search results.
- `retrieve`: return a node through catalog retrieval, MCP, or agent APIs.
- `export`: include content in frozen/static/agent exports.
- `author`: read and edit source in authoring workflows.
- `publish`: move lifecycle state into or out of public output.
- `configure`: change mount, site, or delivery configuration.
- `administer`: inspect or mutate operational/admin-only surfaces.

## Visibility

Lifecycle visibility sets the minimum role for read-like permissions:

| Visibility | Minimum role for read/search/retrieve/export |
|------------|----------------------------------------------|
| `public` | `anonymous` |
| `unlisted` | `reader` |
| `internal` | `reader` |
| `private` | `reader` |
| `draft` | `contributor` |
| `archived` | `admin` |

Author, publish, configure, and administer permissions use their role minimums
even on public pages.

## Mount Policy

Mounts can declare access in `mounts.yaml`. Mount policy is evaluated before page
policy, so it can protect an entire documentation boundary.

```yaml
mounts:
  - id: partner
    label: Partner Docs
    content_root: content/partner
    url_prefix: /partner
    access:
      visibility: internal
      teams: [partners]
```

The example requires a signed-in reader on the `partners` team before any page in
the mount can be read, searched, retrieved, or exported.

## Page Policy

Pages can declare access in front matter. Page policy combines with lifecycle
visibility and mount policy.

```yaml
---
title: Launch Plan
visibility: private
access:
  teams: [launch]
---
```

This page requires at least `reader` plus membership in the `launch` team.

```yaml
---
title: Site Operations
access:
  admin_only: true
---
```

This page is admin-only even if its lifecycle visibility would otherwise be
public. Admin-only and team-restricted pages are excluded from public output by
default.

## Evaluation Rules

Access is allowed only when all of these are true:

1. The subject has the minimum role for the requested permission.
2. The subject has the minimum role implied by lifecycle visibility.
3. If policy lists `roles`, the subject has one of those roles or a stronger role.
4. If policy lists `teams`, the subject belongs to one of those teams, unless the
   subject is an admin.
5. `admin_only: true` requires the `admin` role.

The runtime model exposes mount and page checks through the catalog registry so
output filters can evaluate the same policy for routes, search, catalog JSON,
MCP resources, `llms.txt`, and static exports.

## Public Output Filtering

Public output surfaces evaluate anonymous permissions by default. A page is
omitted when its page policy, lifecycle visibility, or mount policy denies the
required permission.

Filtered public surfaces include:

- browser search snapshots and `search.json`
- `catalog.json`, API operation manifests, `tools.json`, `meta.json`, and
  structure indexes
- `llms.txt` and `llms-full.txt`
- sitemap and static export routes, including `index.txt`
- MCP node resources, node retrieval, graph traversal children, and semantic
  search results

Author/private mode can still build indexes with `include_private=true` so
authors can inspect and validate protected pages locally. Public static output,
GitHub Pages builds, and unauthenticated MCP/browser surfaces do not use that
escape hatch.
