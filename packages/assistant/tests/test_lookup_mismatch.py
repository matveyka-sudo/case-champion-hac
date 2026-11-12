"""A mismatched result lookup must not fill the next prompt with stale data."""

import json

import pytest
from test_assistant import Backend, assistant, explain, reply, tool


@pytest.mark.asyncio
async def test_mismatched_lookup_is_compact_and_does_not_authorize_its_metrics():
    backend = Backend(
        {
            "analysis_id": "analysis",
            "result_hash": "wrong-hash",
            "metrics": [{"metric_id": "foreign", "text": "x" * 6000}],
        }
    )
    agent, transport, _, _ = assistant(
        [
            reply(calls=[tool()], finish_reason="tool_calls"),
            reply(),
        ],
        backend=backend,
    )
    result = await explain(agent)
    assert result.status == "generated" and len(transport.calls) == 2
    data = json.loads(transport.calls[1]["messages"][-1]["content"])["untrusted_tool_data"]
    assert data == {"status": "unknown", "reason_code": "SAVED_LOOKUP_MISMATCH"}
    assert "foreign" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_existing_http_bridge_presentation_remains_supported():
    from test_assistant import analysis
    from test_freeform import answer

    saved = analysis()
    result, *_ = await answer(
        saved,
        "Что известно о причинах задержек?",
        "explain",
        findings=[{"metric_id": "travel-time", "explanation_code": "saved_value"}],
        responses=[
            reply(
                {
                    "paragraph": "Начнём с того, что действительно сохранено: [[metric:travel-time]]. По этому значению нельзя установить причину задержек.",
                    "findings": [{"metric_id": "travel-time", "explanation_code": "saved_value"}],
                }
            )
        ],
    )
    assert result.status == "generated"
    assert result.findings[0].claims[0].value == 1800
