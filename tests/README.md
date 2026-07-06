# Test domains

Tests are named for product behavior rather than delivery waves. Put new coverage
in the narrowest existing domain; create another behavior-named module when a
file would otherwise mix unrelated fixtures or ownership.

| Domain | Primary modules |
| --- | --- |
| Access and authorization | `test_author_authorization.py`, `test_chirp_docs_rbac.py`, `test_visibility_audit.py` |
| Authoring and browser behavior | `test_author_sse_browser.py`, `test_chirp_docs_runtime.py`, `test_chirp_docs_view_lint.py` |
| Catalog graph and structure | `test_chirp_docs_graph_query.py`, `test_chirp_docs_graph_structure.py`, `test_chirp_docs_federation_and_semantic_search.py` |
| Search and retrieval | `test_chirp_docs_search.py`, `test_chirp_docs_catalog_surfaces.py`, `test_chirp_docs_federation_and_semantic_search.py` |
| Delivery, freeze, and export | `test_chirp_docs_static_export.py`, `test_catalog_packaging.py`, `test_chirp_docs_catalog_surfaces.py` |
| References, inventories, and editions | `test_chirp_docs_reference_resolution.py`, `test_chirp_docs_editions_and_chunks.py`, `test_chirp_docs_link_and_inventory_contracts.py` |
| Sources, formats, and migration | `test_chirp_docs_sources.py`, `test_chirp_docs_mdx_migration.py`, `test_chirp_docs_content_ir.py` |
| Internationalization and tenancy | `test_chirp_docs_i18n.py`, `test_chirp_docs_tenancy_paths.py` |
| Templates, themes, and response contracts | `test_chirp_docs_template_stack.py`, `test_chirp_docs_theming.py`, `test_chirp_docs_response_conformance.py` |
| CLI, agents, and MCP | `test_fura_cli_standalone.py`, `test_cli_command_modules.py`, `test_cli_entrypoint_smoke.py` |

The seven former wave-named modules contain 62 collected tests. Their behavior-
named replacements must continue to collect the same 62 tests unless a future
change explicitly documents an intentional consolidation.
