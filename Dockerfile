# Railway production image. The official Python images do not publish a 3.14t
# tag, so uv installs CPython's free-threaded build explicitly.
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.10.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHON_GIL=0 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    FURA_MODE=preview \
    FURA_WORKERS=1 \
    FURA_SKIP_CONTRACT_CHECKS=1

WORKDIR /app

RUN uv python install 3.14t

# Cache third-party dependencies separately from project source.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project --python 3.14t

ARG FURA_BASE_URL=""
ENV FURA_BASE_URL=$FURA_BASE_URL

COPY . .
RUN uv sync --locked --no-dev --python 3.14t \
    && python -c 'import sys; from furatena.catalog.docs_app import DocsApp; assert not sys._is_gil_enabled(), "Furatena requires a GIL-disabled runtime"' \
    && fura --app-root /app/app freeze --full --workers 1

EXPOSE 8000
CMD ["/app/scripts/railway-start.sh"]
