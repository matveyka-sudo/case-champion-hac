"""TEST_ONLY: saved tool and HTTP egress boundaries with canonical evidence groups."""

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from a2_api.api.application import create_app
from a2_api.assistant.explanations import explain
from a2_api.assistant.tools import SavedTools
from a2_assistant.core import FlashAssistant, render_explanation
from a2_assistant.tools import ToolRejected
from a2_assistant.transport import FlashConfig, ModelReply
from a2_contracts.common import canonical_hash
from a2_contracts.v2.assistant import Explanation
from a2_contracts.v2.results import AnalysisResult
from test_v2_tools import ScopedSavedService, scope


def grouped_service(members=2):
    service = ScopedSavedService()
    raw = service.saved.model_dump(mode="json")
    prototype = raw["evidence_refs"][0]
    leaves = sorted(
        [prototype["evidence_id"], *(f"TEST_ONLY-leaf-{i:04}" for i in range(1, members))]
    )
    raw["evidence_refs"] = [prototype | {"evidence_id": leaf} for leaf in leaves]
    group_id = "evidence-group:" + canonical_hash(leaves)
    raw["evidence_groups"] = [{"group_id": group_id, "evidence_ids": leaves}]
    for metric in raw["metrics"]:
        metric["source_refs"] = [group_id]
    for score in raw["scores"]:
        score["evidence_refs"] = [group_id]
    service.saved = AnalysisResult.model_validate(raw)
    return service, leaves


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["get_analysis", "get_metric_details"])
async def test_saved_tools_expand_group_refs_before_existing_payload_caps(name):
    service, leaves = grouped_service(61)
    original = service.saved.model_dump_json()
    args = {"analysis_id": service.saved.analysis_id}
    if name == "get_metric_details":
        args["metric_id"] = service.saved.metrics[0].metric_id
    result = await SavedTools(service).call(name, args, scope(service))
    rows = [result] if name == "get_metric_details" else result["metrics"]
    assert rows and rows[0]["source_refs"] == leaves[:20]
    assert rows[0]["references_truncated"] is True
    if name == "get_analysis":
        assert result["scores"][0]["evidence_refs"] == leaves[:20]
        assert result["scores"][0]["references_truncated"] is True
    assert "evidence-group:" not in json.dumps(result)
    assert service.saved.model_dump_json() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["missing", "tamper", "foreign"])
async def test_saved_tools_reject_broken_group_maps_before_exposing_any_facts(attack):
    service, _ = grouped_service()
    if attack == "missing":
        service.saved.evidence_groups.clear()
    elif attack == "tamper":
        service.saved.evidence_groups[0].evidence_ids.append("foreign")
    else:
        service.saved.metrics[0].source_refs.append("foreign")
    with pytest.raises(ToolRejected):
        await SavedTools(service).call(
            "get_analysis", {"analysis_id": service.saved.analysis_id}, scope(service)
        )


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


class Transport:
    live = False

    def __init__(self, saved, leaves):
        self.calls = []
        metric = saved.metrics[0]
        self.response = ModelReply(
            content=json.dumps(
                {
                    "paragraph": "Разберём сохранённый показатель: [[metric:"
                    + metric.metric_id
                    + "]]. [[coverage]]",
                    "findings": [
                        {"metric_id": metric.metric_id, "explanation_code": "saved_value"}
                    ],
                    "evidence_ids": leaves,
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


def explanation_service(saved, leaves, private):
    transport, budget = Transport(saved, leaves), Budget()
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
    return service, transport, budget, backend


@pytest.mark.asyncio
@pytest.mark.parametrize("private", [False, True])
async def test_grouped_http_explanation_keeps_private_facts_local_and_public_witness_private(
    private,
):
    source, leaves = grouped_service()
    saved = source.saved
    service, transport, budget, backend = explanation_service(saved, leaves, private)
    returned = []

    async def service_explain(*args):
        result = await explain(service, *args)
        returned.append(result)
        return result

    service.explain = service_explain
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analyses/" + saved.analysis_id + "/explanation",
            json={"question": "Какие ограничения у сохранённого показателя?"},
        )
    assert response.status_code == 200, response.text
    parsed = Explanation.model_validate_json(response.content)
    assert parsed.evidence_refs == leaves
    assert "evidence-group:" not in response.text and "witness" not in response.text
    assert service.analysis.await_count == 2 and not backend.call.await_count
    assert render_explanation(returned[0], saved)
    if private:
        assert parsed.reason_code == "PRIVATE_SOURCE_EXTERNAL_MODEL_DISABLED"
        assert not transport.calls and not budget.reservations
        assert not service.accepted_snapshot.await_count and not service.proposals.await_count
    else:
        assert parsed.status == "generated"
        assert len(transport.calls) == len(budget.reservations) == 1
        assert "evidence-group:" not in json.dumps(transport.calls)
        with pytest.raises(ValueError, match="narrative_invalid"):
            render_explanation(parsed, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"evidence_groups": []}, {"full_access": True}])
async def test_http_question_cannot_supply_group_maps_or_broaden_access(extra):
    source, leaves = grouped_service()
    service, transport, budget, _ = explanation_service(source.saved, leaves, False)
    service.explain = AsyncMock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/analyses/" + source.saved.analysis_id + "/explanation",
            json={"question": "Объясни показатель"} | extra,
        )
    assert response.status_code == 422
    assert not service.explain.await_count and not transport.calls and not budget.reservations
