# Railway production image. The official Python images do not publish a 3.14t
# tag, so uv installs CPython's free-threaded build explicitly.
FROM python:3.14-slim@sha256:d3400aa122fa42cf0af0dbe8ec3091b047eac5c8f7e3539f7135e86d855dc015

ARG FURA_RUNTIME_UID=65532
ARG FURA_RUNTIME_GID=65532

COPY --from=ghcr.io/astral-sh/uv:0.10.8@sha256:88234bc9e09c2b2f6d176a3daf411419eb0370d450a08129257410de9cfafd2a /uv /usr/local/bin/uv

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git gosu passwd \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid "$FURA_RUNTIME_GID" furatena \
    && useradd --uid "$FURA_RUNTIME_UID" --gid "$FURA_RUNTIME_GID" \
        --no-create-home --home-dir /tmp/furatena-home --shell /usr/sbin/nologin furatena \
    && install -d --mode 0755 /opt/python /opt/venv \
    && install -d --mode 0750 --owner "$FURA_RUNTIME_UID" \
        --group "$FURA_RUNTIME_GID" /data/furatena

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHON_GIL=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp/furatena-home \
    XDG_CACHE_HOME=/tmp/furatena-cache \
    FURA_RUNTIME_UID=$FURA_RUNTIME_UID \
    FURA_RUNTIME_GID=$FURA_RUNTIME_GID \
    FURA_DISTRIBUTION=private-image \
    FURA_MODE=preview \
    FURA_WORKERS=1 \
    FURA_SERVER_WORKERS=1 \
    FURA_SKIP_CONTRACT_CHECKS=1

WORKDIR /app

RUN uv python install --install-dir /opt/python 3.14t

# Cache third-party dependencies separately from project source.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --python 3.14t

ARG FURA_BASE_URL=""
ARG RAILWAY_GIT_COMMIT_SHA=""
ARG FURA_BUILD_GIT_SHA=$RAILWAY_GIT_COMMIT_SHA
ARG FURA_IMAGE_VERSION="development"
ARG FURA_IMAGE_CHANNEL="development"
ARG FURA_PR_PREVIEW=""
ARG FURA_PREVIEW_PR_NUMBER=""
ARG FURA_PREVIEW_SHA=""
ARG FURA_PREVIEW_REVIEW_URL=""
ARG FURA_PREVIEW_ORIGIN=""
ARG RAILWAY_PUBLIC_DOMAIN=""
LABEL org.opencontainers.image.title="Furatena" \
    org.opencontainers.image.description="Proprietary Furatena documentation platform runtime" \
    org.opencontainers.image.source="https://github.com/lbliii/furatena" \
    org.opencontainers.image.revision=$FURA_BUILD_GIT_SHA \
    org.opencontainers.image.version=$FURA_IMAGE_VERSION \
    com.furatena.image.channel=$FURA_IMAGE_CHANNEL
ENV FURA_BASE_URL=$FURA_BASE_URL \
    FURA_BUILD_GIT_SHA=$FURA_BUILD_GIT_SHA \
    FURA_IMAGE_VERSION=$FURA_IMAGE_VERSION \
    FURA_IMAGE_CHANNEL=$FURA_IMAGE_CHANNEL \
    FURA_PR_PREVIEW=$FURA_PR_PREVIEW \
    FURA_PREVIEW_PR_NUMBER=$FURA_PREVIEW_PR_NUMBER \
    FURA_PREVIEW_SHA=$FURA_PREVIEW_SHA \
    FURA_PREVIEW_REVIEW_URL=$FURA_PREVIEW_REVIEW_URL \
    FURA_PREVIEW_ORIGIN=$FURA_PREVIEW_ORIGIN \
    RAILWAY_PUBLIC_DOMAIN=$RAILWAY_PUBLIC_DOMAIN

COPY . .
RUN uv sync --locked --no-dev --python 3.14t \
    && python -c 'import os, sys; from furatena.catalog.docs_app import DocsApp; assert os.path.realpath(sys.executable).startswith("/opt/python/"), "Furatena requires the application-owned interpreter"; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"' \
    && fura --app-root /app/app freeze --full --workers 1 \
    && chown -R "$FURA_RUNTIME_UID:$FURA_RUNTIME_GID" /app/app/.docs-cache \
    && install -d --mode 0750 --owner "$FURA_RUNTIME_UID" \
        --group "$FURA_RUNTIME_GID" /app/.context \
    && rm -f /usr/local/bin/uv

EXPOSE 8000
USER ${FURA_RUNTIME_UID}:${FURA_RUNTIME_GID}
CMD ["/app/scripts/railway-start.sh"]
