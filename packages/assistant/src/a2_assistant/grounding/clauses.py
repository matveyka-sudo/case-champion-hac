"""Server-owned factual clauses and a closed legacy presentation language.

This is a finite template check, not semantic entailment of arbitrary prose.
Source text and the question cannot extend the accepted language.
"""

import re
from typing import Annotated, Literal, cast

from a2_contracts.v2.assistant import Finding
from a2_contracts.v2.results import AnalysisResult
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .narrative import AnswerMode, conversational_finding

ClauseId = AnswerMode | Literal["components", "time_scope"]


def factual_clause(
    mode: ClauseId,
    saved: AnalysisResult,
    metric_ids: list[str],
    score_ids: list[str],
    *,
    has_proposal: bool,
) -> Finding:
    if mode in {"components", "time_scope"}:
        result = conversational_finding(
            "help", saved, metric_ids, score_ids, has_proposal=has_proposal
        )
        metrics = {m.metric_id: m for m in saved.metrics}
        scores = {s.score_id: s for s in saved.scores}
        if mode == "components":
            if (
                not score_ids
                or not metric_ids
                or not all(
                    any(
                        c.kind == "metric" and c.id == mid
                        for sid in score_ids
                        for c in scores[sid].components
                    )
                    for mid in metric_ids
                )
            ):
                raise ValueError("No selected component relationship")
            text = "Выбранные показатели связаны сохранёнными компонентами расчёта."
        else:
            if any(metrics[mid].status != "available" for mid in metric_ids):
                raise ValueError("Unknown saved time")
            kinds = {metrics[mid].time_kind for mid in metric_ids}
            if kinds == {"movement"}:
                text = (
                    "Сохранено время движения; оно не подтверждает полное время грузовой доставки."
                )
            elif kinds == {"complete_chain"}:
                text = "Сохранено время полной цепочки в области этого расчёта."
            else:
                raise ValueError("No unambiguous saved time kind")
        # Keep all server-owned scope warnings from the canonical rendering.
        from .narrative import _safety

        result.text = " ".join(
            part
            for part in (
                text,
                _safety(saved, [metrics[m] for m in metric_ids] + [scores[s] for s in score_ids]),
            )
            if part
        )
        return result
    return conversational_finding(
        cast(AnswerMode, mode), saved, metric_ids, score_ids, has_proposal=has_proposal
    )


# These are presentation/epistemic templates, never transport explanations.
# FACT/ATOM are reserved sentinels generated locally after placeholder validation.
_PRESENTATION = frozenset(
    {
        "Предварительный показатель FACT",
        "Начнём с того, что действительно сохранено: FACT",
        "По этому значению нельзя установить причину задержек",
        "FACT",
        "ATOM",
        "Сохранено FACT",
        "Сохранены FACT и FACT",
        "Для выбранной точки сохранено FACT",
        "Для выбранного показателя сохранено FACT",
        "Для выбранной оценки сохранено FACT",
        "Сохранённое время: FACT",
        "Разберём сохранённый показатель: FACT",
        "Для чтения результата начните с FACT",
        "Здесь важно отделить численную оценку от объяснения её причины",
        "Здесь полезно разделить наблюдение и вывод: сохранено FACT",
        "Выбор места требует понять, что именно сравнивается: сохранены FACT и FACT",
        "Сначала сопоставим сами записи: FACT и FACT",
        "Начну с фактически записанного пути: FACT",
        "По этому итогу нельзя установить качество дорог: для такого вывода нужны данные о самой цепочке",
        "Качество дорог и стоимость переезда из этих записей установить нельзя",
        "Причину задержек по длительности участка установить нельзя",
        "Здесь нет доказанного минимума",
        "Нельзя утверждать что данные полные",
        "Не могу гарантировать полный охват",
        "Это не рейтинг территорий",
        "Это не реальная оценка города",
        "Этот итог не является доказанным минимумом",
        "По этому итогу нельзя установить качество дорог",
        "Сохранённые данные не описывают дорожное покрытие",
        "По этим данным нельзя установить состояние дорог",
        "Сведений о ремонте дорог нет данных в расчёте",
    }
)


def legacy_presentation(paragraph: str, placeholders: list[str]) -> bool:
    """Accept only complete templates; concatenating known words proves nothing."""
    if "FACT" in paragraph or "ATOM" in paragraph:
        return False
    for token in placeholders:
        paragraph = paragraph.replace(token, "FACT")
    for atom in ("comparison", "coverage", "methodology", "journey"):
        paragraph = paragraph.replace("[[" + atom + "]]", ".ATOM.")
    for sentence in re.split(r"[.!?\n]+", paragraph):
        sentence = " ".join(sentence.split())
        if not sentence:
            continue
        if sentence in _PRESENTATION:
            continue
        # Plain domain filler is inert and retained for legacy character budgets.
        if set(sentence.casefold().split()) <= {
            "данные",
            "данны",
            "данн",
            "дан",
            "да",
            "д",
            "fact",
            "atom",
        }:
            continue
        return False
    return True


class _FindingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    metric_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    score_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    explanation_code: Literal[
        "saved_value", "travel_time", "unknown", "not_applicable", "pending", "methodology_pending"
    ]

    @model_validator(mode="after")
    def one_reference(self) -> "_FindingSelection":
        if (self.metric_id is None) == (self.score_id is None):
            raise ValueError("Select exactly one saved metric or score")
        return self


class _ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer_mode: AnswerMode | None = None
    clause_id: ClauseId | None = None
    paragraph: Annotated[str, Field(min_length=1, max_length=650)] | None = None
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=15
    )
    findings: list[_FindingSelection] = Field(min_length=1, max_length=20)
    template_ids: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=10
    )
