# Safe HTML boundaries

Furatena does not grant trust in templates. Author-controlled values are escaped by
default; rich values cross a named producer boundary and use Kida's `Markup` type.
Templates must not use an undocumented `|safe` filter. `fura check` rejects one unless
it carries a specific `safe(reason="...")` justification.

## Boundary inventory

| Value | Producer | Sanitizer or proof | Consumers | Classification |
| --- | --- | --- | --- | --- |
| Page, collection, API, and author-preview bodies | Markdown/RST/MDX/HTML adapters and autodoc | `sanitize_rendered_html()` removes executable elements, event/style/srcdoc attributes, and unsafe URL schemes before node registration | document views, collection sections, author studio | trusted renderer HTML |
| Nested directive bodies and tab panels | Patitas child renderer or syntax highlighter | parent catalog output crosses `sanitize_rendered_html()`; nested insertion uses `trusted_renderer_fragment()` | directive partials | trusted renderer fragment |
| Directive titles, descriptions, table cells, and tab labels | `render_inline_text()` / `render_inline_cell()` | author text is escaped before the limited `strong`, `em`, `code`, `a`, and `mark` tags are inserted | directive partials | escaped limited markup |
| Directive icons | `render_icon_html()` | strict icon-name grammar plus lookup in the vendored SVG directory; author SVG or traversal paths are rejected | cards, dropdowns, tabs | vendored static SVG |
| Search titles, labels, headings, and snippets | `highlight_search_terms()` | the complete value is HTML-escaped before `<mark>` insertion | search full page and HTMX results | escaped limited markup |
| JSON-LD | `json_ld_script()` | `json.dumps()` plus escaping for `<`, `>`, `&`, U+2028, and U+2029 prevents script-boundary breakout | head metadata and OOB head updates | script-safe JSON |
| Plain front matter and literalinclude captions | configuration/source parser | no trusted type; normal Kida escaping | headings, metadata, captions | plain text |

## Raw HTML policy

Raw HTML is accepted as source input for compatibility, but it is not accepted as a
trust assertion. All adapter output passes through the same sanitizer. `script`,
`style`, `iframe`, `object`, `embed`, and `template` elements are removed with their
contents. Event-handler, inline-style, `srcdoc`, and unsafe-scheme URL attributes are
removed. Use supported Markdown/directive structures for interactive content.

## Residual risk

The sanitizer intentionally preserves renderer-owned element and non-executable
attribute vocabulary so directives and documentation layouts remain extensible. It is
not a replacement for CSP, output encoding, or URL validation at other sinks. Vendored
SVG content and Furatena-owned templates remain reviewed code; changing either requires
the same security review as changing Python or JavaScript. Malicious fixtures cover full
pages, HTMX fragments, search, JSON-LD, author preview, directive arguments, and raw HTML.
