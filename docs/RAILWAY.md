# Railway deployment

Furatena runs on Railway as one free-threaded, frozen-catalog replica. The
Docker build installs CPython `3.14t`, verifies that importing the server stack
does not enable the GIL, and runs `fura freeze`. The runtime serves that artifact
in preview mode; source indexing and author mutations are not exposed.

## Service configuration

The canonical service is linked to `lbliii/furatena`, branch `main`. Railway's
GitHub source integration rebuilds on every merge. `railway.toml` selects the
Dockerfile, one replica, and `/readyz` as the admission healthcheck.

Set `FURA_BASE_URL` to the service's HTTPS origin. The Dockerfile declares it as
a build argument as well as a runtime variable so frozen canonical links and
on-request responses agree.

`PYTHON_GIL=0`, `FURA_MODE=preview`, and `FURA_WORKERS=1` are fixed in the image.
The start script refuses to boot if the imported application stack has enabled
the GIL.

## Deploy

From a clean `main` checkout:

```console
railway up --detach -m "Deploy Furatena frozen docs service"
railway deployment list --service furatena --environment production --json
```

Do not treat a queued deployment as complete. Wait for the newest deployment to
reach `SUCCESS`, then verify the domain and runtime.

## Smoke runbook

Replace `$ORIGIN` with the Railway HTTPS origin and require every command to
succeed:

```console
curl --fail --silent --show-error "$ORIGIN/healthz"
curl --fail --silent --show-error "$ORIGIN/readyz"
curl --fail --silent --show-error "$ORIGIN/catalog/query.json"
curl --fail --silent --show-error "$ORIGIN/search/semantic?q=deployment"
curl --fail --silent --show-error "$ORIGIN/tools.json"
curl --fail --silent --show-error "$ORIGIN/llms.txt"
curl --fail --silent --show-error "$ORIGIN/sitemap.xml"
curl --fail --silent --show-error \
  -H 'Accept: text/markdown' "$ORIGIN/docs/get-started/"
```

Also confirm the server process itself is free-threaded:

```console
railway ssh --service furatena --environment production -- \
  python -c 'import sys; print(sys._is_gil_enabled())'
```

The required output is `False`.
