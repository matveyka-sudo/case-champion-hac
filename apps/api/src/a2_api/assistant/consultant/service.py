"""Waydex consultant: short plain answers about a saved analysis, districts and addresses."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from a2_contracts.v2.assistant import Explanation, ExplanationRequest, Finding
from a2_contracts.v2.results import AnalysisResult

from a2_api.assistant.consultant import catalog, rules
from a2_api.assistant.consultant.facts import build_facts, describe
from a2_api.assistant.consultant.model import ModelClient, settings_from_env

logger = logging.getLogger("a2_api.consultant")
_REF = re.compile(r"\s*\[[^\]]{1,120}\]")


class Consultant:
    def __init__(self, model: ModelClient | None = None) -> None:
        self.model = model

    @classmethod
    def from_env(cls) -> Consultant:
        settings = settings_from_env()
        return cls(ModelClient(settings) if settings is not None else None)

    async def aclose(self) -> None:
        if self.model is not None:
            await self.model.aclose()

    async def explain(
        self,
        service: Any,
        result: AnalysisResult,
        request: ExplanationRequest,
        request_id: str,
    ) -> Explanation:
        question = (request.question or "").strip() or "Что показывает этот расчёт?"
        facts = build_facts(service, result, request.selection)
        districts = catalog.districts()
        q = " " + rules.norm(question) + " "
        address = None
        if catalog.address_query(question) is not None:
            address = await asyncio.to_thread(
                catalog.walk_from_address, question, rules.address_categories_in(q)
            )
        draft = rules.answer(question, facts, districts, address)
        text = None
        if self.model is not None:
            context = describe(
                facts,
                districts=_relevant_districts(q, facts, districts),
                address=catalog.address_text(address) if address is not None else None,
            )
            text = await self.model.ask(question, context, draft.text)
        if text:
            text = _REF.sub("", text).replace("**", "").strip()
            answer = rules.Answer(text, draft.metric_ids, draft.score_ids)
            _link_mentions(answer, facts)
            return _explanation(
                result, request_id, answer, self.model.model_id if self.model else None
            )
        reason = "DISABLED" if self.model is None else "PROVIDER_UNAVAILABLE"
        return _explanation(result, request_id, draft, None, reason)


def _relevant_districts(
    q: str, facts: Any, districts: list[catalog.DistrictFact]
) -> list[catalog.DistrictFact]:
    if not districts:
        return []
    asks = rules.has(q, *rules.DISTRICT) or rules.find_district(q, districts) is not None
    if not asks:
        return []
    picked = rules._pick_districts(q, facts, districts)
    return sorted(picked, key=lambda d: d.value if d.value is not None else -1, reverse=True)


def _link_mentions(answer: rules.Answer, facts: Any) -> None:
    """Attach the point a model answer names, so its chip selects it on the map."""
    if answer.metric_ids or answer.score_ids:
        return
    text = answer.text.lower()
    for point in facts.ranked_points():
        if point.score_id and re.search(rf"\b{re.escape(point.label.lower())}\b", text):
            answer.score_ids.append(point.score_id)
            return


def _explanation(
    result: AnalysisResult,
    request_id: str,
    answer: rules.Answer,
    model_id: str | None,
    reason: str | None = None,
) -> Explanation:
    metrics = {m.metric_id: m for m in result.metrics}
    scores = {s.score_id: s for s in result.scores}
    metric_ids = list(dict.fromkeys(i for i in answer.metric_ids if i in metrics))[:20]
    score_ids = list(dict.fromkeys(i for i in answer.score_ids if i in scores))[:20]
    findings = []
    for number, chunk in enumerate(_chunks(answer.text)):
        refs = number == 0
        values = {
            "text": chunk,
            "metric_ids": metric_ids if refs else [],
            "evidence_refs": [],
            "claims": [],
            "score_ids": score_ids if refs else [],
            "score_claims": [],
        }
        findings.append(Finding.model_validate(values))
    return Explanation(
        analysis_id=result.analysis_id,
        analysis_result_hash=result.result_hash,
        request_id=request_id,
        status="generated" if model_id is not None else "template",
        model_id=model_id,
        reason_code=None if model_id is not None else (reason or "DISABLED"),
        findings=findings[:20],
        proposals=[],
        evidence_refs=[],
        limitations=[],
    )


def _chunks(text: str, limit: int = 1000) -> list[str]:
    text = text.strip() or "Нет ответа."
    parts: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n") if p.strip()):
        while len(paragraph) > limit:
            cut = paragraph.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            parts.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            parts.append(paragraph)
    return parts
