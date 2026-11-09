FROM ghcr.io/astral-sh/uv:0.12.17 AS uv
FROM python:3.12-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/ apps/api/
COPY packages/ packages/
COPY config/product.json config/product.json
ENV UV_PYTHON_DOWNLOADS=0 PYTHONDONTWRITEBYTECODE=1
RUN uv lock --check && uv sync --frozen --package a2-api --no-default-groups
USER 10001:10001
CMD ["/app/.venv/bin/uvicorn", "a2_api.app:create_configured_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
