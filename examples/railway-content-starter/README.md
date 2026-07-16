# Furatena Railway content starter

This repository contains adopter-owned public content and presentation
configuration for the proprietary Furatena Railway template. It does not
contain the Furatena application or require a Python package.

1. Create a public repository from this directory.
2. Edit Markdown below `app/content/` and commit it.
3. Set the Railway template's `FURA_CONTENT_REPOSITORY` to the HTTPS clone URL.
4. Pushes can call the authenticated refresh endpoint by configuring the
   `FURATENA_REFRESH_URL` and `FURATENA_REFRESH_TOKEN` repository secrets.

The `main` ref is the normal content channel. Maintainers of the conformance
fixture also keep a `conformance-v2` tag so the template harness can prove
update and rollback behavior.
