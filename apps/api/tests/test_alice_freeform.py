"""Real saved-tool bridge with an offline model; no provider or database calls."""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2_api.assistant.tools import SavedTools
from a2_assistant import AccessScope, FlashAssistant, FlashConfig
from a2_assistant.transport import ModelReply, ModelToolCall
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_alice_runtime import SyntheticDurableBudget


def two_points():
    root = Path(__file__).resolve().parents[3]
    raw = json.loads(
        (root / "packages/contracts/fixtures/v2/urban_passenger-result.json").read_bytes()
    )["payload"]
    raw["metrics"].append(
        raw["metrics"][0]
        | {
            "metric_id": "other-time",
            "subject": raw["metrics"][0]["subject"] | {"point_id": "other-point"},
        }
    )
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "other-score",
            "value": 25.0,
            "subject": raw["scores"][0]["subject"] | {"point_id": "other-point"},
            "components": [{"kind": "metric", "id": "other-time"}],
        }
    )
    return AnalysisResult.model_validate(raw)


class Model:
    live = False

    def __init__(self, saved, target):
        self.saved, self.target, self.calls = saved, target, []

    async def complete(self, messages, tools, **kwargs):
        self.calls.append(deepcopy(messages))
        if len(self.calls) == 1:
            return ModelReply(
                content=None,
                finish_reason="tool_calls",
                input_tokens=20,
                output_tokens=20,
                tool_calls=[
                    ModelToolCall(
                        id="lookup",
                        name="get_analysis",
                        arguments=json.dumps(
                            {
                                "analysis_id": self.saved.analysis_id,
                                "selection": {
                                    "entity_kind": "sample_point",
                                    "entity_id": self.target,
                                },
                            }
                        ),
                    )
                ],
            )
        return ModelReply(
            content=json.dumps(
                {
                    "answer_mode": "compare",
                    "findings": [
                        {"score_id": s.score_id, "explanation_code": "saved_value"}
                        for s in self.saved.scores
                    ],
                }
            ),
            finish_reason="stop",
            input_tokens=20,
            output_tokens=20,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target,generated,altered",
    [
        ("other-point", True, None),
        ("not-in-this-result", False, None),
        ("other-point", False, "hash"),
        ("other-point", True, "value"),
    ],
)
async def test_selected_comparison_requires_authorized_bounded_saved_entity_lookup(
    target, generated, altered
):
    saved = two_points()
    original = saved.model_dump_json()
    service = SimpleNamespace(
        repository=None,
        analysis=AsyncMock(return_value=saved),
        accepted_snapshot=AsyncMock(return_value=None),
    )
    tools = SavedTools(service)
    original_call = tools.call

    async def intercepted(name, args, access):
        data = await original_call(name, args, access)
        if altered == "hash":
            data["result_hash"] = "TEST_ONLY-other-saved-version"
        elif altered == "value":
            data["scores"][0]["value"] = 99.0
        return data

    tools.call = intercepted
    model, budget = Model(saved, target), SyntheticDurableBudget()
    agent = FlashAssistant(
        FlashConfig(
            api_key="TEST_ONLY",
            folder_id="TEST_ONLY",
            tools_enabled=True,
            max_model_calls=2,
            max_retries=0,
        ),
        transport=model,
        tool_backend=tools,
        deployment_budget=budget,
    )
    result = await agent.explain(
        saved,
        access_scope=AccessScope(
            principal_id="owner",
            analysis_ids={saved.analysis_id},
            territory_ids={saved.request.city_id},
            source_ids={e.source_id for e in saved.evidence_refs},
            snapshot_ids={saved.snapshot_id},
        ),
        registered_template_ids=set(),
        request_id="offline-freeform",
        context=ExplanationRequest(
            question="Сравни выбранную точку с другой указанной точкой из этого сохранённого результата",
            selection={"entity_kind": "sample_point", "entity_id": "demo-point"},
        ),
    )
    assert (result.status == "generated") is generated
    assert len(model.calls) == len(budget.reservations) == 2
    assert saved.model_dump_json() == original
    if generated:
        assert [c.value for c in result.findings[0].score_claims] == [50, 25]
        assert "первого" in result.findings[0].text
        lookup = json.loads(model.calls[1][-1]["content"])["untrusted_tool_data"]
        assert [s["score_id"] for s in lookup["scores"]] == ["other-score"]
        assert "value" not in lookup["scores"][0]
    else:
        assert result.reason_code == "INVALID_EXPLANATION"
        assert all(c.score_id != "other-score" for f in result.findings for c in f.score_claims)
