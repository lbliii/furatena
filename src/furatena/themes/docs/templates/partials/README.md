# Theme partial shadows

Overrides of framework partials (`catalog/_templates/partials/`). First match
wins in the template loader stack — see [../../VIEWS.md](../../VIEWS.md).

| Partial | Role |
|---------|------|
| `docs_shell_nav.html` | Site top bar for app-surface pages |
| `doc_article.html` | Doc body + backlinks footer |

Block layouts live in `theme/layouts/`:

| Layout | Used by |
|--------|---------|
| `docs_catalog.html` | `doc`, `doc_list`, `collection`, `changelog` views |
| `docs_app.html` | `home`, `search`, `portal`, `page` views |

Project-level overrides: `templates/partials/` (same filenames).
