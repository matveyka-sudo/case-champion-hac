"""Stable ASGI entrypoint and historical factory imports."""

from fastapi import FastAPI

from a2_api.api.application import create_app as create_v2_app
from a2_api.api.legacy import API_CONTRACT_VERSION as API_CONTRACT_VERSION
from a2_api.api.legacy import BundleQuery as BundleQuery
from a2_api.api.legacy import IdempotencyKey as IdempotencyKey
from a2_api.api.legacy import create_app as create_app
from a2_api.api.legacy import error_response as error_response


def create_configured_app() -> FastAPI:
    """Uvicorn factory. Configuration must be explicitly selected by environment."""
    from a2_api.api.bootstrap import build_app

    return build_app()


app = create_v2_app()
