"""TEST_ONLY: natural saved-result prose through the existing service and HTTP DTO."""

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.assistant.explanations import explain
from a2_assistant.core import FlashAssistant, render_explanation
from a2_assistant.transport import FlashConfig, ModelReply
from a2_contracts.v2.assistant import Explanation
from a2_contracts.v2.results import AnalysisResult


class Transport:
    live = False

    def __init__(self, metric):
        self.calls = []
        self.response = ModelReply(
            content=json.dumps(
                {
                    "paragraph": "Начнём с того, что действительно сохранено: [[metric:"
                    + metric.metric_id
                    + "]]. По этому значению нельзя установить причину задержек.",
                    "findings": [
                        {"metric_id": metric.metric_id, "explanation_code": "saved_value"}
                    ],
                    "evidence_ids": metric.source_refs[:5],
                }
            ),
            tool_calls=[],
            input_tokens=100,
            output_tokens=100,
            finish_reason="stop",
        )

    async def complete(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class Budget:
    durable = False
    currency = "RUB"
    limit_amount = Decimal("1")

    def __init__(self):
        self.reservations = []

    async def reserve(self, **kwargs):
        self.reservations.append(kwargs)
        return True

    async def record_usage(self, **kwargs):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_existing_http_route_serializes_natural_prose_and_keeps_private_facts_local(private):
    root = Path(__file__).resolve().parents[3]
    saved = AnalysisResult.model_validate(
        json.loads(
            (root / "packages/contracts/fixtures/v2/urban_passenger-result.json").read_bytes()
        )["payload"]
    )
    transport, budget = Transport(saved.metrics[0]), Budget()
    backend = SimpleNamespace(call=AsyncMock(side_effect=AssertionError("No tools required")))
    assistant = FlashAssistant(
        FlashConfig(api_key="TEST_ONLY-key", folder_id="TEST_ONLY-folder"),
        transport=transport,
        tool_backend=backend,
        deployment_budget=budget,
    )
    repository = SimpleNamespace(
        private_source_bindings=AsyncMock(return_value=[object()] if private else [])
    )
    service = SimpleNamespace(
        analysis=AsyncMock(return_value=saved),
        assistant=assistant,
        _repository=lambda: repository,
        accepted_snapshot=AsyncMock(return_value=None),
        proposals=AsyncMock(return_value=[]),
    )
    returned = []

    async def service_explain(*args):
        explanation = await explain(service, *args)
        returned.append(explanation)
        return explanation

    service.explain = service_explain
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analyses/" + saved.analysis_id + "/explanation",
            json={"question": "Что можно понять о причинах задержек по этому пути?"},
        )
    assert response.status_code == 200, response.text
    parsed = Explanation.model_validate_json(response.content)
    assert parsed.findings == returned[0].findings
    assert "witness" not in response.text and "_GroundedExplanation" not in response.text
    assert service.analysis.await_count == 2
    assert not backend.call.await_count
    if private:
        assert parsed.status == "template"
        assert parsed.reason_code == "PRIVATE_SOURCE_EXTERNAL_MODEL_DISABLED"
        assert not transport.calls and not budget.reservations
    else:
        assert parsed.status == "generated"
        assert "Начнём с того" in parsed.findings[0].text
        assert len(transport.calls) == len(budget.reservations) == 1
        assert render_explanation(returned[0], saved)
        with pytest.raises(ValueError, match="narrative_invalid"):
            render_explanation(parsed, saved)
