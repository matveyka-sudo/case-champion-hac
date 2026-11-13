"""Waydex consultant questions that are not tied to a saved analysis."""

from fastapi import APIRouter, Request

from a2_api.api.dependencies import backend
from a2_api.assistant.consultant import Consultant
from a2_api.assistant.consultant.service import ConsultantAnswer, ConsultantQuestion

router = APIRouter()


@router.post("/consultant/ask")
async def ask(body: ConsultantQuestion, request: Request) -> ConsultantAnswer:
    consultant = getattr(backend(request), "consultant", None) or Consultant()
    return await consultant.ask(body)
