"""Canonical production HTTP boundary. Calculations run only in the worker."""

from typing import Annotated, Literal

from a2_contracts.v2.api import (
    Capabilities,
    TerritoryOption,
)
from fastapi import APIRouter, Query, Request

from a2_api.api.dependencies import backend

router = APIRouter()


@router.get("/capabilities")
async def capabilities(request: Request) -> Capabilities:
    return backend(request).capabilities()


@router.get("/territories")
async def territories(
    request: Request,
    level: Literal["city", "region"] | None = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> list[TerritoryOption]:
    return backend(request).registry.territories(level, query)
