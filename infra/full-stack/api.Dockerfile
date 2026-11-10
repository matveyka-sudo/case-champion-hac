FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv
FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY apps/api/ apps/api/
COPY packages/ packages/
ENV UV_PYTHON_DOWNLOADS=0 UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 A2_DATA_DIR=/app/data
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked uv lock --check && uv sync --frozen --package a2-api --no-default-groups && mkdir /observations && chown 10001:10001 /observations
COPY config/ config/
COPY data/bundles/ data/bundles/
COPY data/manifests/ data/manifests/
COPY data/directories/ data/directories/
COPY infra/real-e2e/runtime.py infra/real-e2e/runtime.py
COPY infra/full-stack/runtime.py infra/full-stack/runtime.py
COPY infra/full-stack/bundle.py infra/full-stack/bundle.py
USER 10001:10001
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "a2_api.api.bootstrap:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
