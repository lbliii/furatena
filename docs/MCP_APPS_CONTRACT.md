# MCP Apps contract

Furatena MCP Apps contract version 1 follows the stable MCP Apps specification
dated `2026-01-26`. This contract defines discovery, identity, access, gateway,
and fallback rules. Furatena ships the public catalog-search App at
`ui://furatena/catalog-search/v1`; trusted-author Apps and WebMCP remain outside
this contract.

## Negotiation and compatibility

The extension identifier is `io.modelcontextprotocol/ui`. A client opts in by
including this initialize capability:

```json
{
  "capabilities": {
    "extensions": {
      "io.modelcontextprotocol/ui": {
        "mimeTypes": ["text/html;profile=mcp-app"]
      }
    }
  }
}
```

Furatena acknowledges the same extension and MIME type only when the client
offers it. An absent extension, a missing `mimeTypes` array, or an unsupported
MIME type is an ordinary non-App MCP session. Tools remain available without UI
metadata and return their existing text plus `structuredContent` result.

Contract v1 uses the stable nested tool link `_meta.ui.resourceUri`. The legacy
flat `_meta["ui/resourceUri"]` field is rejected as stale. Additive metadata is
compatible. Removing or renaming a resource, changing its access policy, or
changing the linked tool result shape requires a new Furatena contract version
and an explicit compatibility decision through `fura agent-diff`.

The upstream protocol source is the [stable MCP Apps
specification](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx).

## Resource identity and content

Canonical resource URIs have this form:

```text
ui://furatena/<app-id>/v1
```

The authority and path are stable lowercase namespaces; the final segment is
the Furatena contract version. Credentials, ports, query parameters, and
fragments are forbidden because they create cache, review, and identity
ambiguity. A resource descriptor and the content returned by `resources/read`
use `text/html;profile=mcp-app`. The content is a bundled HTML5 document, not an
external URL. Resource content repeats the same `_meta.ui` and
`_meta["io.furatena/mcp-app"]` security metadata as its descriptor so a host can
enforce policy at the rendering boundary.

The Furatena metadata block is:

```json
{
  "contractVersion": 1,
  "specVersion": "2026-01-26",
  "canonicalUri": "ui://furatena/catalog-search/v1",
  "audience": "public",
  "redaction": "public-only",
  "fallback": "structured-content"
}
```

The canonical URI remains present after gateway rewriting. This gives contract
diffs, audits, and caches a stable upstream identity.

## Tool links and fallback

A UI-enabled tool links to exactly one predeclared resource:

```json
{
  "name": "semantic_search",
  "_meta": {
    "ui": {
      "resourceUri": "ui://furatena/catalog-search/v1",
      "visibility": ["model", "app"]
    }
  }
}
```

Both visibility values are explicit. `model` preserves normal agent discovery
and the non-App fallback; `app` permits the sandboxed App on the same MCP server
connection to call the tool. The linked resource must be reachable through
`resources/read`. UI-only tools are outside v1: they would remove the ordinary
MCP fallback and create a second authorization surface.

The App also calls the existing `retrieve_node` and `query_graph` tools. They do
not carry a `resourceUri`, so invoking either tool does not create another App
or make it a primary rendering entry point. Under the stable specification,
omitted `_meta.ui.visibility` defaults to `["model", "app"]`; therefore both
tools remain model-visible and are callable by an App on the same server
connection. Hosts must still reject cross-server App calls. Furatena relies on
that standard default instead of attaching a misleading catalog-search resource
link to secondary tools.

Every linked tool continues to return meaningful text and
`structuredContent`. The App is presentation only: search, retrieval, graph
querying, validation, access checks, redaction, rate limiting, output bounds,
and auditing remain owned by the existing MCP tool path.

## Catalog-search App

The bundled catalog-search document is dependency-free HTML, CSS, and
JavaScript. It renders search ranking, active filters, bounded result cards,
keyword and semantic scores, tags, provenance, and structured repair
diagnostics. Result actions call the existing `retrieve_node` and `query_graph`
tools on the same connection. The App never fetches an external URL and inserts
tool data with DOM text nodes rather than HTML interpretation.

The search form caps a request at 25 results. The renderer also caps host-sent
results at 25 and parses at most 100,000 characters from the text fallback.
`structuredContent` is preferred; ordinary JSON text remains usable by hosts
that do not render Apps. The packaged HTML SHA-256 is compiled into the server,
checked on every resource read, and recorded in Furatena contract metadata so
content cannot drift behind the versioned URI silently.

App discovery and the `semantic_search` tool link are connection-scoped. They
appear only after the client negotiates the stable MIME type. The App resource
and every App link are omitted entirely from include-private author sessions;
this registration boundary is a security control, not a presentation choice.
Search, retrieval, and graph calls still use the public access subject and
`include_private=false`, so the App cannot observe private identities, facets,
metadata, snippets, provenance, or counts. A future trusted-author App requires
a separate versioned resource and security review.

## Access and redaction

| Resource audience | Required redaction | Discovery |
| --- | --- | --- |
| `public` | `public-only` | Public sessions only in contract v1 |
| `trusted-author` | `session-authorized` | Denied in contract v1 |

Contract v1 uses conservative isolation: include-private trusted-author
sessions expose no App resources or App links, including the public App. This
keeps browser-capable and author-capable connections disjoint until a separately
versioned trusted-author App receives a security review.

Public is the default projection. A public App may not receive private node
identities, filters, metadata, snippets, provenance, or aggregate counts. Those
values are filtered before the tool result crosses the MCP boundary, not hidden
in browser code. Trusted-author resources default to denied and require a
distinct versioned URI plus the existing author-mode, include-private, role,
token, rate-limit, and audit gates. A trusted session does not convert a public
resource into a private one.

App HTML is data-free and contains no catalog records, credentials, actor data,
or authorization state. Tool results are never cached across access subjects.
MCP Apps do not weaken confirmation or dry-run requirements for mutating tools.

## Browser security metadata

Each resource explicitly declares all four CSP arrays under `_meta.ui.csp`:
`connectDomains`, `resourceDomains`, `frameDomains`, and `baseUriDomains`. Empty
arrays are the deny-by-default value. Origins must be exact HTTPS origins;
`connectDomains` may also contain exact WSS origins. Credentials, paths, query
parameters, fragments, and unrestricted `*` origins are invalid. Wildcard
subdomains remain valid where the upstream specification permits them, but
should be avoided when an exact origin is available.

Protocol adapters may serialize four empty arrays as an empty `csp` object, as
the stable specification defines missing domain lists and empty domain lists as
the same deny-by-default request. Furatena's canonical descriptor and fixtures
retain the explicit arrays for contract review; either wire representation
authorizes no origin.

`permissions` is an explicit object. Contract v1 recognizes only the upstream
`camera`, `microphone`, `geolocation`, and `clipboardWrite` keys, each with an
empty-object value. The catalog-search App requests none. A dedicated App
`domain`, when used, must be an exact HTTPS origin. Hosts remain responsible for
iframe sandboxing and may further restrict CSP or permissions.

## Gateway rewriting and collisions

A gateway rewrites both the resource descriptor URI and every linked
`resourceUri` with this deterministic form:

```text
ui://<gateway-authority>/<gateway-namespace>/<upstream-authority>/<app-id>/v1
```

For example, `ui://furatena/catalog-search/v1` in namespace `team-docs` becomes
`ui://gateway.example/team-docs/furatena/catalog-search/v1`. The upstream
authority remains in the path, and `_meta["io.furatena/mcp-app"].canonicalUri`
retains the original URI. The gateway records its `authority` and `namespace`
in a `gateway` object in that metadata block.

Namespaces are stable opaque URI segments assigned per upstream server; they
are not derived from user-controlled display names. A gateway must build the
complete rewritten registry before publishing it. If two inputs produce the
same rewritten resource URI or tool identity, publication fails. First-wins,
last-wins, and suffix-on-collision behavior are forbidden because they make
tool links nondeterministic and can cross access boundaries.

## Diagnostics and fixtures

`fura check --agent` and `fura check --agent-only` validate App metadata when it
is present. The inventory groups them under `fura.agent.mcp_app.*`; findings use
these stable rule ids:

- `fura.agent.mcp_app.missing_metadata`
- `fura.agent.mcp_app.stale_contract`
- `fura.agent.mcp_app.unsafe_metadata`
- `fura.agent.mcp_app.unreachable_resource`

The public fixture in `tests/fixtures/agent-contracts/v1/public.json` contains a
complete deny-by-default catalog-search contract. The trusted-author fixture
records inheritance of ordinary public MCP resources while exposing no App
resource or App link; trusted Apps remain denied until a separately reviewed
implementation exists. WebMCP and authoring UI behavior are outside this
contract.

The test suite exercises direct handler negotiation, Milo in-process hosting,
real gateway URI rewriting and link preservation, and newline-delimited stdio
transport reads. Non-App clients are checked separately to keep the progressive
text and `structuredContent` fallback usable.
