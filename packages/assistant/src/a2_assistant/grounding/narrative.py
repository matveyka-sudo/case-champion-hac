"""Question-specific prose assembled only from immutable saved DTO facts.

The model chooses intent and references. It never writes factual prose, performs
arithmetic, supplies a cause, or authorizes an operation. The same assembly is
repeated before rendering, so mutable Explanation objects cannot forge a claim.
"""

from typing import Literal, get_args

from a2_contracts.v2.assistant import Finding, NumericClaim, ScoreClaim
from a2_contracts.v2.evidence import EvidenceResolver, require_result_evidence
from a2_contracts.v2.results import AnalysisResult, Metric, Score

from ..evidence import leaf_references

AnswerMode = Literal[
    "explain", "methodology", "journey", "coverage", "improve", "compare", "help", "unsupported"
]
ANSWER_MODES = get_args(AnswerMode)


def _methodology(saved: AnalysisResult, facts: list[Metric | Score]) -> str:
    if getattr(saved.request, "cargo_transport", None) == "pipeline":
        return (
            "Загрузка трубопровода сопоставляет сохранённый объём с мощностью только "
            "для одинаковых продукта, периода и единиц. Это отдельный показатель "
            "подтверждённого направления, а не индекс доставки. Он не доказывает "
            "наличие свободной коммерческой мощности."
        )
    if saved.analysis_kind == "urban_passenger":
        return (
            "Городская оценка уменьшается с ростом полного времени пути. "
            "H выбирается по классу административной площади города, а не по размеру "
            "выделенного участка или окна карты. В индексе категории время должно быть "
            "подтверждённым минимумом всей цепочки. Итог точки учитывает выбранные "
            "категории и их приоритеты, а территориальный итог — исходные веса населения."
        )
    if saved.analysis_kind == "intercity_passenger":
        if {fact.subject.mode for fact in facts} == {"private_car"}:
            return (
                "Автомобильная оценка сопоставляет сохранённое эталонное время с временем "
                "автомобильного пути. Она рассматривается отдельно от общественного транспорта; "
                "ожидание первого рейса к ней не добавляется. Эталон и время маршрута "
                "должны быть подтверждены для принятых условий."
            )
        return (
            "Пассажирская оценка учитывает отдельно время пути относительно сохранённого "
            "эталона и ожидание первого отправления. Автомобиль сравнивается отдельно. "
            "Без подтверждённого эталона и календаря нельзя обещать полный итог."
        )
    if saved.analysis_kind == "urban_cargo":
        return (
            "Грузовой доступ оценивается по полному времени до совместимого узла. "
            "Важны подтверждённый подъезд и операции; приём и отправка разделены. "
            "H зависит от площади города. Одна площадка не представляет весь город."
        )
    return (
        "Доставка оценивается по полной цепочке с ожиданием и обработкой и по выбранной "
        "срочности. Этот норматив задаёт половинное снижение оценки, а не запрет "
        "более долгой доставки. Одного времени движения недостаточно. Трубопроводная загрузка "
        "является отдельным показателем и не входит в индекс доставки."
    )


def _coverage(facts: list[Metric | Score]) -> str:
    if any(f.reason_code == "NO_FEASIBLE_ROUTE" for f in facts):
        if not all(
            f.reason_code == "NO_FEASIBLE_ROUTE"
            and f.completeness == "full"
            and f.subject == facts[0].subject
            and (
                isinstance(f, Score)
                and f.status == "available"
                and f.value == 0
                and bool(f.evidence_refs)
                or isinstance(f, Metric)
                and f.status == "not_applicable"
                and f.value is None
                and bool(f.source_refs)
            )
            for f in facts
        ):
            return (
                "В выбранных показателях смешаны разные состояния или области. "
                "Отметка об отсутствии маршрута относится только к её сохранённой области; "
                "она не подтверждает отсутствие цепочки для остальных показателей. "
                "Доступные значения и неизвестные данные следует рассматривать отдельно."
            )
        return (
            "В сохранённой полной области подтверждено отсутствие подходящей цепочки. "
            "Такой ноль является отдельным результатом проверки, а не измеренным временем пути. "
            "Его нельзя переносить на другие даты и условия."
        )
    if any(f.status != "available" or f.value is None for f in facts):
        if any("POPULATION" in (f.reason_code or "") for f in facts):
            return (
                "Основа для взвешивания населения не подтверждена полностью. "
                "Поэтому известные точки не подменяют территориальный итог. "
                "Неизвестное значение не означает ноль."
            )
        if any("TAU" in (f.reason_code or "") for f in facts):
            return (
                "Число доступных объектов зависит от отдельного порога времени. "
                "В сохранённом расчёте этот порог не подтверждён; H его не заменяет. "
                "Неизвестное количество не означает отсутствие объектов."
            )
        if any("MINIMUM" in (f.reason_code or "") for f in facts):
            return (
                "Минимальное время не подтверждено, поэтому индекс остаётся неизвестным. "
                "Найденный маршрут сам по себе не доказывает ближайший по времени объект. "
                "Неизвестное значение не означает ноль."
            )
        if any(f.status == "pending" for f in facts):
            return "Показатель ещё ожидает отдельного расчёта; готового численного ответа пока нет."
        if any(f.status == "not_applicable" for f in facts):
            return "Часть показателей неприменима к этим условиям; это не численная нулевая оценка."
        return (
            "Для выбранного показателя нет полного подтверждённого набора данных. "
            "По этому результату нельзя установить отсутствующий факт или причину. "
            "Неизвестное значение не означает ноль."
        )
    if any(f.completeness != "full" for f in facts):
        return (
            "Известные значения сохранены, но полнота данных ограничена. "
            "Они описывают принятые условия и не доказывают полноту всех объектов и путей города."
        )
    return (
        "Выбранные показатели отмечены как полные в сохранённой области расчёта. "
        "Это не расширяет область на другие даты, режимы и территории. "
        "Рейтинг допустим только при сопоставимых условиях и разрешённой полноте."
    )


def _comparison(facts: list[Metric | Score]) -> str:
    if len(facts) != 2:
        return "Для сравнения нужны два конкретных сохранённых показателя; уточните вторую точку."
    first, second = facts
    if (
        type(first) is not type(second)
        or first.name != second.name
        or getattr(first, "unit", None) != getattr(second, "unit", None)
        or first.subject.kind != second.subject.kind
        or first.subject.category != second.subject.category
        or first.subject.mode != second.subject.mode
        or getattr(first, "calculation_kind", None) != getattr(second, "calculation_kind", None)
    ):
        return (
            "Эти показатели различаются по смыслу, единицам или режиму. "
            "Сравнивать их как одну оценку некорректно; выберите одноимённые показатели."
        )
    if any(f.status != "available" or f.value is None for f in facts):
        return (
            "Сравнение неполное: хотя бы одно сохранённое значение неизвестно или неприменимо. "
            "Порядок и численную разницу по отсутствующим данным установить нельзя."
        )
    assert first.value is not None and second.value is not None
    relation = (
        "Сохранённые значения равны."
        if first.value == second.value
        else "У второго показателя сохранённое значение выше."
        if second.value > first.value
        else "У первого показателя сохранённое значение выше."
    )
    return relation + (
        " Это сравнение только этих двух записей одного расчёта, а не рейтинг города. "
        "Причину различия без данных о цепочках и покрытии я не придумываю."
    )


# Every Leg.kind of the v2 journey contract; an unknown future kind stays generic.
_LEG_KINDS = {
    "walk": "пеший участок",
    "ride": "поездка",
    "wait_initial": "ожидание первого отправления",
    "wait_transfer": "ожидание при пересадке",
    "transfer_operation": "пересадка",
    "handling": "обработка",
}


def _journey(saved: AnalysisResult, facts: list[Metric | Score]) -> str:
    ids = {f.journey_id for f in facts if isinstance(f, Metric) and f.journey_id is not None}
    linked = [journey for journey in saved.journeys if journey.journey_id in ids]
    if len(linked) != 1 or not linked[0].legs:
        return (
            "В выбранных показателях нет одной однозначно связанной сохранённой цепочки "
            "с участками. Выберите показатель времени конкретного пути: по одному индексу "
            "нельзя определить, где возникло ожидание или задержка."
        )
    leg = max(linked[0].legs, key=lambda item: item.duration_seconds)
    description = _LEG_KINDS.get(leg.kind, "участок транспортной цепочки")
    return (
        f"Среди записанных участков этой цепочки наибольшую продолжительность имеет {description}. "
        "Это описание сохранённого пути, а не прогноз задержек и не доказательство "
        "минимального времени. Другие цепочки с ним не смешиваются."
    )


def _explain_details(metrics: list[Metric], scores: list[Score]) -> str:
    """What the saved structure itself says about the selected facts; no causes."""
    parts = []
    if any(score.components for score in scores):
        parts.append(
            "Значение собрано из сохранённых компонентов расчёта; "
            "каждый можно выбрать и разобрать отдельно. "
        )
    kinds = {metric.time_kind for metric in metrics if metric.status == "available"}
    if kinds == {"movement"}:
        parts.append("Это время движения; полное время цепочки оно не подтверждает. ")
    elif kinds == {"complete_chain"}:
        parts.append("Это время полной цепочки в области этого расчёта. ")
    return "".join(parts)


def _proven_observed_auxiliary(fact: Metric | Score) -> bool:
    return (
        isinstance(fact, Score)
        and fact.name == "observed_route_accessibility"
        and fact.status == "not_applicable"
        and fact.value is None
        and fact.completeness == "full"
        and fact.reason_code == "USE_PROVEN_CATEGORY_INDEX"
    )


def _safety(saved: AnalysisResult, facts: list[Metric | Score]) -> str:
    notes = []
    if (
        saved.snapshot_id.startswith("demo-")
        or "DEMO_SYNTHETIC_TRANSPORT_MODEL" in saved.limitations
        or "synthetic_only" in saved.limitations
    ):
        notes.append("Это синтетическая демонстрационная модель, не рейтинг реального города.")
    observed = [f for f in facts if f.name == "observed_route_accessibility"]
    if any(f.status == "available" and f.value is not None for f in observed):
        notes.append(
            "Это оценка одного найденного маршрута: минимум не доказан, "
            "она не заменяет индекс категории."
        )
    if any(f.value is None and not _proven_observed_auxiliary(f) for f in observed):
        notes.append(
            "По недоступной предварительной оценке нельзя подтвердить минимальное время "
            "или отсутствие маршрута: минимум не доказан, она не заменяет индекс категории."
        )
    if any(_proven_observed_auxiliary(f) for f in observed):
        notes.append(
            "Предварительная оценка найденного маршрута здесь неприменима; "
            "используйте сохранённый индекс категории."
        )
    if any(f.completeness != "full" for f in facts):
        notes.append("Полнота данных ограничена; вывод остаётся предварительным.")
    if any(f.value is None and not _proven_observed_auxiliary(f) for f in facts):
        notes.append("Неизвестное значение не означает ноль.")
    if any(isinstance(f, Score) and not f.ranking_eligible for f in facts):
        notes.append("Этот показатель не допущен к рейтингу территорий.")
    return " ".join(notes)


def conversational_finding(
    mode: AnswerMode,
    saved: AnalysisResult,
    metric_ids: list[str],
    score_ids: list[str],
    *,
    has_proposal: bool,
    resolver: EvidenceResolver | None = None,
) -> Finding:
    resolver = resolver or require_result_evidence(saved)
    metrics = {m.metric_id: m for m in saved.metrics}
    scores = {s.score_id: s for s in saved.scores}
    selected_metrics = [metrics[identifier] for identifier in metric_ids]
    selected_scores = [scores[identifier] for identifier in score_ids]
    facts: list[Metric | Score] = [*selected_metrics, *selected_scores]
    if mode == "methodology":
        text = _methodology(saved, facts)
    elif mode == "coverage":
        text = _coverage(facts)
    elif mode == "compare":
        text = _comparison(facts)
    elif mode == "journey":
        text = _journey(saved, facts)
    elif mode == "improve":
        if has_proposal:
            text = (
                "К ответу приложен разрешённый сохранённый сценарий; его можно проверить отдельно. "
                "Эффект проверяется отдельным сценарным расчётом. "
                "Я не запускаю его из чата и не обещаю повышения индекса."
            )
        else:
            text = (
                "В этом сохранённом результате нет разрешённого сценария улучшения. "
                "По одному значению индекса нельзя подтвердить, какое изменение поможет "
                "и насколько. "
            )
            linked_ids = {metric.journey_id for metric in selected_metrics}
            if getattr(saved.request, "cargo_transport", None) == "pipeline":
                text += (
                    "Для рекомендации нужны подтверждённые данные о направлении, продукте, "
                    "объёме и мощности и отдельная допустимая проверка изменения."
                )
            elif any(
                journey.journey_id in linked_ids and journey.status == "available" and journey.legs
                for journey in saved.journeys
            ):
                text += (
                    "Можно разобрать длительность участков выбранной сохранённой цепочки. "
                    "Причины задержек и эффект изменений требуют отдельных оснований."
                )
            else:
                text += (
                    "Для предметной рекомендации нужны подтверждённая цепочка пути "
                    "с составляющими времени и отдельный сценарный расчёт."
                )
            text += " После подготовки сценария эффект проверяется отдельно."
    elif mode == "help":
        text = (
            "Можно спросить своими словами о сохранённой оценке, методике, времени по участкам "
            "или ограничениях данных. Выбранная точка задаёт контекст. Сопоставить можно "
            "конкретные сохранённые показатели; сценарий проверяется отдельно по кнопке."
        )
    elif mode == "unsupported":
        text = (
            "В сохранённом расчёте нет оснований для такого вывода. "
            "Я не определяю виновных, цены или будущие эффекты по одному индексу. "
            "Могу разобрать выбранный показатель, его цепочку и подтверждённые ограничения."
        )
    else:
        text = (
            (
                "Оценка связана с полным временем в принятой модели: более долгий путь "
                "снижает доступность при тех же условиях. "
                if saved.analysis_kind == "urban_passenger" and selected_scores
                else "Здесь показан выбранный сохранённый показатель. "
            )
            + _explain_details(selected_metrics, selected_scores)
            + _coverage(facts)
        )
    text = " ".join(part for part in (text, _safety(saved, facts)) if part)
    refs = list(
        dict.fromkeys(
            leaf_references(
                (
                    ref
                    for fact in facts
                    for ref in (
                        fact.source_refs if isinstance(fact, Metric) else fact.evidence_refs
                    )
                ),
                resolver,
            )
        )
    )[:50]
    return Finding(
        text=text,
        metric_ids=metric_ids,
        score_ids=score_ids,
        evidence_refs=refs,
        claims=[
            NumericClaim(metric_id=m.metric_id, value=m.value, unit=m.unit)
            for m in selected_metrics
        ],
        score_claims=[ScoreClaim(score_id=s.score_id, value=s.value) for s in selected_scores],
    )


def verified_conversational_finding(
    finding: Finding,
    saved: AnalysisResult,
    *,
    has_proposal: bool,
    resolver: EvidenceResolver | None = None,
) -> bool:
    # The common legacy path must not repeatedly scan a large saved city result.
    if not finding.text.startswith(
        (
            "Загрузка трубопровода",
            "Городская оценка",
            "Пассажирская оценка",
            "Автомобильная оценка",
            "Грузовой доступ",
            "Доставка оценивается",
            "В сохранённой полной области",
            "В выбранных показателях смешаны",
            "Основа для взвешивания",
            "Число доступных объектов",
            "Минимальное время",
            "Показатель ещё",
            "Часть показателей",
            "Для выбранного",
            "Известные значения",
            "Выбранные показатели",
            "Для сравнения",
            "Эти показатели",
            "Сравнение неполное",
            "Сохранённые значения",
            "У второго",
            "У первого",
            "В выбранных показателях",
            "Среди записанных",
            "К ответу",
            "К этому ответу",
            "В этом сохранённом результате",
            "Можно спросить",
            "В сохранённом расчёте",
            "Оценка связана",
            "Здесь показан",
        )
    ):
        return False
    return any(
        conversational_finding(
            mode,
            saved,
            finding.metric_ids,
            finding.score_ids,
            has_proposal=has_proposal,
            resolver=resolver,
        )
        == finding
        for mode in ANSWER_MODES
    )
