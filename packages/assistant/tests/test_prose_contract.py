"""Offline rendering budgets and fixed diagnostics; no provider is involved."""

import json
import logging

import pytest
from a2_assistant import core
from a2_assistant.prose import ProseRejected, generated_finding, prose_limits
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, assistant, reply, scope, score_analysis


def observed_saved(*, long_ids=False):
    raw = score_analysis(value=84.05771144540653, partial=True).model_dump(mode="json")
    raw["limitations"] = ["DEMO_SYNTHETIC_TRANSPORT_MODEL"]
    raw["scores"][0].update(name="observed_route_accessibility")
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "unknown",
            "name": "category_index",
            "status": "unknown",
            "value": None,
            "reason_code": "MINIMUM_NOT_PROVEN",
        }
    )
    raw["metrics"] = analysis().model_dump(mode="json")["metrics"]
    raw["metrics"][0].update(
        metric_id="time",
        name="best_observed_time",
        value=1.2345678901234567e200,
        completeness="partial",
    )
    if long_ids:
        for number, fact in enumerate([*raw["metrics"], *raw["scores"]]):
            fact["metric_id" if "metric_id" in fact else "score_id"] = chr(97 + number) * 180
    return AnalysisResult.model_validate(raw)


@pytest.mark.parametrize("long_ids", [False, True])
@pytest.mark.parametrize("atom", ["comparison", "coverage", "methodology", "journey"])
def test_advertised_budget_renders_with_long_values_all_scope_warnings_and_each_atom(
    long_ids, atom
):
    saved = observed_saved(long_ids=long_ids)
    facts = [*saved.metrics, *saved.scores]
    limits = prose_limits(saved, facts)
    tokens = " ".join(core.fact_placeholder(fact) for fact in facts) + " [[" + atom + "]]"
    # Fill every available qualitative character, or the raw 650-character cap.
    remaining = min(limits.qualitative_max_characters - 4, 650 - len(tokens) - 1)
    # Saved-domain filler: model prose must stay within the grounded vocabulary.
    paragraph = tokens + " " + ("данные " * remaining)[:remaining]
    finding = generated_finding(
        paragraph,
        saved,
        [saved.metrics[0].metric_id],
        [s.score_id for s in saved.scores],
        [],
        has_proposal=False,
        limits=limits,
    )
    assert len(paragraph) <= 650 and len(finding.text) <= 1000
    if long_ids:
        assert len(paragraph) == 650
    assert finding.claims[0].value == saved.metrics[0].value
    assert [claim.value for claim in finding.score_claims] == [84.05771144540653, None]
    assert finding.evidence_refs == ["evidence"]
    assert "Это синтетическая" in finding.text and "минимум не доказан" in finding.text
    assert "Полнота данных ограничена" in finding.text
    assert "Неизвестное значение не означает ноль" in finding.text
    assert "не допущен к рейтингу" in finding.text


@pytest.mark.asyncio
async def test_response_example_and_advertised_contract_round_trip_offline():
    saved = observed_saved()
    agent, transport, backend, budget = assistant([reply()])

    async def echo_example():
        payload = json.loads(transport.calls[-1]["messages"][1]["content"])
        # The legacy prose budget is validated by the server, not advertised.
        assert "response_contract" not in payload
        assert "atom" not in transport.calls[-1]["messages"][0]["content"]
        return reply(payload["response_example"])

    transport.responses = [echo_example]
    result = await agent.explain(
        saved,
        access_scope=scope(),
        registered_template_ids=set(),
        request_id="TEST_ONLY",
        context=ExplanationRequest(
            question="Объясни найденную оценку и её ограничения",
            selection={"entity_kind": "sample_point", "entity_id": "point"},
        ),
    )
    assert result.status == "generated" and len(result.findings[0].text) <= 1000
    assert len(transport.calls) == len(budget.reservations) == 1 and not backend.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paragraph,rule",
    [
        (
            "Сохранено [[score:accessibility]]. [[methodology]] [[coverage]] [[journey]]",
            "atom_limit",
        ),
        ("[[score:accessibility]] [[score:accessibility]]", "placeholder_repeated"),
        ("[[score:accessibility]] 999", "literal_number"),
        ("[[score:accessibility]] сто", "written_number"),
        ("[[score:accessibility]] [[score:foreign]]", "markup"),
        ("[[score:accessibility]] " + "я" * 600, "qualitative_budget"),
    ],
)
async def test_narrative_rejections_have_fixed_safe_rules_and_preserve_fallback(
    paragraph, rule, caplog
):
    saved = observed_saved()
    data = {
        "paragraph": paragraph,
        "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
        "evidence_ids": [],
    }
    agent, transport, _, budget = assistant([reply(data)])
    with caplog.at_level(logging.WARNING, logger="a2_assistant"):
        result = await agent.explain(
            saved, access_scope=scope(), registered_template_ids=set(), request_id="TEST_ONLY"
        )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert f"rule={rule}" in caplog.text
    assert paragraph not in caplog.text and "synthetic-key" not in caplog.text
    assert len(transport.calls) == len(budget.reservations) == 1
    assert (
        next(
            c.value
            for f in result.findings
            for c in f.score_claims
            if c.score_id == "accessibility"
        )
        == 84.05771144540653
    )


def test_budget_violation_never_truncates_safety_or_facts():
    saved = observed_saved()
    with pytest.raises(ProseRejected, match="qualitative_budget"):
        generated_finding(
            "[[score:accessibility]] " + "я" * 600,
            saved,
            [],
            ["accessibility"],
            [],
            has_proposal=False,
        )


def test_unknown_diagnostic_rule_cannot_log_arbitrary_model_content():
    assert core._ModelAnswerRejected("narrative_invalid", rule="TEST_ONLY-secret").rule == "other"


@pytest.mark.asyncio
async def test_status_code_mismatch_is_distinct_from_prose_rejection(caplog):
    saved = observed_saved()
    agent, _, _, _ = assistant(
        [reply({"findings": [{"score_id": "accessibility", "explanation_code": "unknown"}]})]
    )
    with caplog.at_level(logging.WARNING, logger="a2_assistant"):
        result = await agent.explain(
            saved, access_scope=scope(), registered_template_ids=set(), request_id="TEST_ONLY"
        )
    assert result.reason_code == "INVALID_EXPLANATION"
    assert "rule=score_status_code" in caplog.text
