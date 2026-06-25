---
title: Views and shell
description: View kinds, templates, and the persistent htmx shell
draft: false
weight: 30
lang: en
type: doc
category: concepts
---

**Shell** — persistent frame (`app/theme/shell.html`): `#main`, search modal, theme menu.

**View** — full page inside `#page-root` selected by front matter `layout:` / `kind:`.

**Partial** — reusable fragment included by views or shell.

**Directive** — rich block inside markdown body (cards, tabs, admonitions).

View resolution order: explicit `view:` → home URL → `docs.yaml` overrides → view kind map → heuristics → default.

Built-in kinds include `doc`, `doc_list`, `home`, `collection`, `changelog`, and `portal`.

See [Views](https://github.com/lbliii/furatena/blob/main/docs/VIEWS.md) in the repository.
