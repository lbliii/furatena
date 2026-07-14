# HTTP QUERY prototype

Furatena exposes an opt-in HTTP `QUERY` projection for complex catalog graph
filters. The ordinary `GET /catalog/query.json?...` route remains the portable
compatibility contract and should be preferred while filters fit comfortably in
a URI.

## Decision

Retain the prototype as a versioned, opt-in request format on the existing
catalog-query resource:

```text
QUERY /catalog/query.json
Content-Type: application/vnd.furatena.catalog-query+json;version=1
Accept: application/json
```

The content is a JSON object using the same filter names and response schema as
the GET endpoint. This avoids a second query language and keeps access control,
pagination, typed graph nodes, and repair errors identical across methods.

The alias `QUERY /graph/query.json` returns `308 Permanent Redirect` to the
canonical path so conforming clients repeat the QUERY request with its content.
`GET /graph/query.json` remains available for compatibility.

## Discovery

`OPTIONS /catalog/query.json` returns:

```text
Allow: GET, HEAD, OPTIONS, QUERY
Accept-Query: application/vnd.furatena.catalog-query+json;version="1"
```

Ordinary GET responses also carry `Accept-Query`, allowing discovery without a
separate OPTIONS request. QUERY requests without `Content-Type` fail with 400;
unsupported types fail with 415; malformed JSON fails with 400; non-object JSON
fails with 422; and an unsatisfied response `Accept` fails with 406.

## Example

```console
curl --request QUERY https://example.test/catalog/query.json \
  --header 'Content-Type: application/vnd.furatena.catalog-query+json;version=1' \
  --header 'Accept: application/json' \
  --data '{"mount":"docs","tag":"api","edge_kind":"link","limit":50}'
```

The equivalent portable request is:

```console
curl 'https://example.test/catalog/query.json?mount=docs&tag=api&edge_kind=link&limit=50'
```

QUERY content and URI query parameters cannot be mixed. This removes ambiguous
precedence and ensures the request-content digest covers every filter.

## Conditional responses and caching

Successful QUERY responses include a strong `ETag` derived from canonicalized
request content and the selected response bytes. Semantically identical JSON
objects with different member order therefore share a validator, while distinct
query content cannot collide merely because the target URI is the same.

Responses use:

```text
Cache-Control: private, max-age=0, must-revalidate
Vary: Accept, Content-Type
```

This permits client-private revalidation while preventing shared intermediaries
that ignore QUERY content from reusing a response for another query. Standard
`If-None-Match` requests receive 304 when the body-aware validator matches.
Furatena does not create stored-query URLs because catalog filters can contain
private selection criteria that should not be copied into logs or bookmarks.

## Portability and fallback

QUERY is safe and idempotent, but browser calls require CORS preflight and some
proxies, WAFs, SDKs, or observability systems may still reject or mishandle the
new method. Clients must treat 405 and 501 responses, connection rejection, or a
missing QUERY capability as reasons to retry with GET when the encoded URI stays
within their transport budget. Large queries that cannot safely fall back should
surface the intermediary limitation rather than silently switching to POST.

The comparison from the bounded prototype is:

| Concern | GET | QUERY |
| --- | --- | --- |
| Broad client/intermediary support | Strong | Emerging |
| Bookmarking and ordinary shared caching | Native | Requires body-aware caches |
| Long or structured filters | URI-limited | Request content |
| Sensitive filter leakage through URI logs | Higher | Lower, subject to body logging policy |
| Browser CORS preflight | Usually unnecessary | Required |
| Furatena response/access contract | Baseline | Identical |

The opt-in remains appropriate for agent and server clients with explicit
capability discovery. GET remains the default in public examples and generated
tool contracts until production intermediary evidence justifies changing that
recommendation.
