# Theme views

Page-level templates registered in `docs.yaml` under `views:`.

Each file here renders a full `#page-root` surface inside the shell. Include
only the chrome that view needs (site nav, docs rail, hero, TOC).

See [../VIEWS.md](../VIEWS.md) for resolution order, view kinds, and how views
differ from partials and directives.

| File | View kind | Surface |
|------|-----------|---------|
| `home.html` | `home` | app |
| `page.html` | `page` | app |
| `portal.html` | `portal` | app |
| `doc.html` | `doc` | catalog |
| `doc_list.html` | `doc_list` | catalog |
| `collection.html` | `collection` | catalog |
| `changelog.html` | `changelog` | catalog |

Override one view without forking the theme: drop `templates/views/doc.html` in
the project root (wins over this directory).
