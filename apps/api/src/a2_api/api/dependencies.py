"""Request-local service lookup; no shared/global service instance."""

from typing import Annotated, cast

from fastapi import Header, Request

from a2_api.services.analyses import AnalysisService

IdempotencyKey = Annotated[str, Header(min_length=1, max_length=200)]


def backend(request: Request) -> AnalysisService:
    return cast(AnalysisService, request.app.state.services)
