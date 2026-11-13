"""Answer questions about accessibility results shown in the life atlas."""
from fastapi import APIRouter, Request

from a2_api.api.dependencies import backend
from a2_api.assistant.consultant import Consultant
from a2_api.assistant.consultant.service import ConsultantAnswer, ConsultantQuestion

router = APIRouter()


@router.post("/address-access/explanation", include_in_schema=False)
async def explain(body: ConsultantQuestion, request: Request) -> ConsultantAnswer:
    consultant = getattr(backend(request), "consultant", None) or Consultant()
    return await consultant.ask(body)
