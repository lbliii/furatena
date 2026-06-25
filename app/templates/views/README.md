# Project view overrides

Sparse overrides for `theme/views/*.html`. First match wins in the template
loader stack.

Example — customize the doc page chrome:

```
templates/views/doc.html
```

Register custom views in `docs.yaml`:

```yaml
views:
  landing: views/landing.html
```

See [../VIEWS.md](../VIEWS.md).
