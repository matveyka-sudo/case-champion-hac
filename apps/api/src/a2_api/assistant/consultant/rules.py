"""Direct answers from the saved numbers; used alone without a model and as its draft."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from a2_api.assistant.consultant.catalog import ADDRESS_LABELS, ADDRESS_NEAREST, DistrictFact
from a2_api.assistant.consultant.facts import (
    CATEGORY_LABELS,
    NEAREST_LABELS,
    Facts,
    band,
    fmt_index,
    fmt_minutes,
    whole,
)

WITH_LABELS = {
    "adult_polyclinic": "взрослыми поликлиниками",
    "child_polyclinic": "детскими поликлиниками",
    "kindergarten": "детскими садами",
    "school": "школами",
    "college_university": "колледжами и вузами",
    "rail_terminal": "ж/д вокзалами",
    "bus_terminal": "автовокзалами",
    "airport": "аэропортами",
    "shopping_center": "торговыми центрами",
    "hospital": "больницами",
    "college": "колледжами",
    "university": "вузами",
    "metro": "метро",
    "mcd": "МЦД",
    "mck": "МЦК",
}
# Urban analysis categories and the address catalogue use different rosters.
TO_ADDRESS = {
    "adult_polyclinic": ("hospital",),
    "child_polyclinic": ("hospital",),
    "school": ("school",),
    "kindergarten": ("kindergarten",),
    "college_university": ("college", "university"),
}


@dataclass
class Answer:
    text: str
    metric_ids: list[str] = field(default_factory=list)
    score_ids: list[str] = field(default_factory=list)


def with_(category: str) -> str:
    """'со школами', 'с вузами'."""
    label = WITH_LABELS.get(category, category)
    hard = label.lower().startswith(("шк", "ст", "ср", "сп", "св", "сл", "вс", "вз", "зв", "мн"))
    return ("со " if hard else "с ") + label


def norm(text: str) -> str:
    return " ".join(re.findall(r"[0-9a-zа-я/]+", text.lower().replace("ё", "е")))


def has(q: str, *words: str) -> bool:
    return any(word in q for word in words)


def categories_in(q: str) -> list[str]:
    found: list[str] = []
    kindergarten = has(
        q,
        "детсад",
        "детский сад",
        "детские сад",
        "детского сад",
        "детских сад",
        "садик",
        " сад",
        "ясли",
    )
    if kindergarten:
        found.append("kindergarten")
    medical = has(
        q, "поликлиник", "больниц", "врач", "медиц", "клиник", "госпитал", "педиатр", "травмпункт"
    )
    if medical:
        if has(q, "детск", "педиатр", "ребен", "детей"):
            found.append("child_polyclinic")
        if not has(q, "детск", "педиатр") or has(q, "взросл"):
            found.append("adult_polyclinic")
    if has(q, "школ"):
        found.append("school")
    if has(q, "колледж", "вуз", "универ", "институт", "техникум", "учеб"):
        found.append("college_university")
    if has(q, "автовокзал", "автостанц", "междугород"):
        found.append("bus_terminal")
    if has(q, "жд", "ж/д", "железнодорож", "поезд", "электричк") or (
        "вокзал" in q and "автовокзал" not in q
    ):
        found.append("rail_terminal")
    if has(q, "аэропорт", "самолет", "аэро"):
        found.append("airport")
    if has(q, "тц", "торгов", "магазин", "молл", "шопинг", "покуп"):
        found.append("shopping_center")
    return found


def address_categories_in(q: str) -> list[str]:
    found: list[str] = []
    for category in categories_in(q):
        for item in TO_ADDRESS.get(category, ()):
            if item not in found:
                found.append(item)
    if has(q, "метро"):
        found.append("metro")
    if has(q, "мцд"):
        found.append("mcd")
    if has(q, "мцк"):
        found.append("mck")
    return found


BEST = (
    "самый доступ",
    "самая доступ",
    "самое доступ",
    "наиболее доступ",
    "лучш",
    "самый удоб",
    "где лучше",
    "выше всего",
    "максимальн",
    "топ",
    "самые доступ",
    "самый транспортно",
)
WORST = (
    "худш",
    "наименее",
    "хуже всего",
    "недоступн",
    "плох",
    "ниже всего",
    "минимальн",
    "проблемн",
    "отста",
    "самый низк",
    "самые низк",
)
DISTRICT = ("район", "округ", "муниципал", "поселени")
TIME = (
    " сколько",
    "как долго",
    "за какое время",
    "время",
    "минут",
    "идти",
    "ехать",
    "добира",
    "далеко",
    "близко",
    "дойти",
    "доехать",
    "пешком",
)
ACCESS = ("насколько", "доступн", "как с ", "как обстоит", "хорошо ли", "есть ли", "как у нас")
INDEX = ("индекс", "оценк", "балл", "рейтинг", "в целом", "итог", "общая", "общий", "результат")
METHOD = (
    "что такое",
    "что значит",
    "как счита",
    "как рассчит",
    "как вычисл",
    "что означает",
    "формул",
    "методик",
    "откуда",
    "как получ",
)
IMPROVE = (
    "улучш",
    "что сделать",
    "как повысить",
    "почему низк",
    "почему так",
    "что не так",
    "слаб",
    "что мешает",
    "как поднять",
)
HELLO = (
    "привет",
    "здравств",
    "добрый",
    "кто ты",
    "что ты умеешь",
    "что умеешь",
    "помоги",
    "что можешь",
    "как пользоваться",
    "хай",
    "hello",
)


def _pick_districts(q: str, facts: Facts, catalog: list[DistrictFact]) -> list[DistrictFact]:
    """Moscow districts by default; okrugs or the oblast only when asked about them."""
    if has(q, "област", "подмосков", "городск округ", "муниципал"):
        return [d for d in catalog if d.municipal]
    if not (has(q, "москв") or "москв" in facts.city.lower() or "москв" in facts.place.lower()):
        return []
    okrugs = has(q, "административн", " ао ", "округ") and not has(q, "район")
    return [d for d in catalog if not d.municipal and d.okrug == okrugs]


def find_district(q: str, catalog: list[DistrictFact]) -> DistrictFact | None:
    best: tuple[int, DistrictFact] | None = None
    for item in catalog:
        words = [w for w in norm(item.name).split() if len(w) >= 4 and w not in {"район", "округ"}]
        if not words:
            continue
        stems = [w[: max(4, len(w) - 2)] for w in words]
        if all(stem in q for stem in stems):
            weight = sum(len(stem) for stem in stems)
            if best is None or weight > best[0]:
                best = (weight, item)
    return best[1] if best else None


def _district_answer(
    q: str, facts: Facts, catalog: list[DistrictFact], worst: bool
) -> Answer | None:
    pool = _pick_districts(q, facts, catalog)
    categories = address_categories_in(q)
    category = categories[0] if categories else None

    def value(item: DistrictFact) -> float | None:
        return item.categories.get(category) if category else item.value

    ranked = sorted(
        (d for d in pool if value(d) is not None), key=lambda d: value(d) or 0, reverse=not worst
    )
    if not ranked:
        return None
    top, rest = ranked[0], ranked[1:3]
    about = f" {with_(category)}" if category else ""
    word = "Хуже всего" if worst else "Лучше всего"
    head = f"{word}{about} — {top.name}: индекс {whole(value(top) or 0)} из 100."
    if rest:
        head += " Следом " + " и ".join(f"{d.name} ({whole(value(d) or 0)})" for d in rest) + "."
    head += " Это пешая доступность по районному расчёту."
    return Answer(head)


def _one_district(item: DistrictFact, q: str) -> Answer:
    categories = address_categories_in(q)
    if categories:
        parts = []
        for category in categories:
            v = item.categories.get(category)
            label = ADDRESS_LABELS.get(category, category)
            parts.append(f"{label} — {'нет данных' if v is None else whole(v)}")
        if len(categories) == 1:
            v = item.categories.get(categories[0])
            label = ADDRESS_LABELS.get(categories[0], categories[0])
            if v is None:
                return Answer(f"{_title(item.name)}: по категории «{label}» данных нет.")
            return Answer(
                f"{_title(item.name)}: {label} — {whole(v)} из 100, {band(v)} пешая доступность."
            )
        return Answer(f"{_title(item.name)}: " + ", ".join(parts) + " (индекс 0–100, пешком).")
    value = item.value
    if value is None:
        return Answer(f"{_title(item.name)}: индекс пока не посчитан.")
    known = {k: v for k, v in item.categories.items() if v is not None}
    text = f"{_title(item.name)}: индекс {whole(value)} из 100, {band(value)} пешая доступность."
    if known:
        strong = max(known, key=lambda k: known[k])
        weak = min(known, key=lambda k: known[k])
        if strong != weak:
            text += (
                f" Лучше всего {with_(strong)} ({whole(known[strong])}), "
                f"хуже всего — {with_(weak)} ({whole(known[weak])})."
            )
    return Answer(text)


def _title(name: str) -> str:
    return name[:1].upper() + name[1:]


def _strong_weak(facts: Facts) -> tuple[str | None, str | None]:
    known = {k: s.index for k, s in facts.summaries.items() if s.index is not None}
    if len(known) < 2:
        return None, None
    return max(known, key=lambda k: known[k] or 0), min(known, key=lambda k: known[k] or 0)


def _overall(facts: Facts) -> Answer:
    known = [
        x for x in facts.summaries.values() if x.index is not None or x.median_minutes is not None
    ]
    if facts.territory_index is None and facts.summaries and not known:
        return Answer(
            f"Для {facts.place} в этом расчёте индексы не посчитаны: нет данных о времени "
            "до объектов. Попробуйте другие условия, категории или способ передвижения."
        )
    if facts.territory_index is None and not facts.summaries:
        if facts.other:
            shown = facts.other[:3]
            text = (
                f"{facts.place}: "
                + "; ".join(f"{label} — {value}" for label, value, _ in shown)
                + "."
            )
            return Answer(text, metric_ids=[i for _, _, i in shown if not i.startswith("score")])
        return Answer(f"Для {facts.place} в этом расчёте нет посчитанных показателей.")
    text = ""
    if facts.territory_index is not None:
        text = (
            f"Общий индекс доступности ({facts.place}, {facts.mode_label}) — "
            f"{fmt_index(facts.territory_index)} из 100, это {band(facts.territory_index)} доступность."
        )
    strong, weak = _strong_weak(facts)
    if strong and weak:
        s, w = facts.summaries[strong], facts.summaries[weak]
        text += (
            f" Лучше всего {with_(strong)} (индекс {fmt_index(s.index)}), "
            f"хуже всего — {with_(weak)} ({fmt_index(w.index)})."
        )
    elif len(known) == 1:
        summary = known[0]
        text += f" До ближайшего объекта ({summary.label}) обычно {fmt_minutes(summary.median_minutes)}."
    ids = [facts.territory_score_id] if facts.territory_score_id else []
    return Answer(text.strip(), score_ids=ids)


def _time(facts: Facts, categories: list[str]) -> Answer | None:
    categories = [c for c in categories if c in facts.summaries] or (
        list(facts.summaries)[:1] if len(facts.summaries) == 1 else []
    )
    if not categories:
        return None
    chosen = facts.selected
    if chosen is not None:
        lines: list[str] = []
        metric_ids: list[str] = []
        score_ids: list[str] = []
        for category in categories:
            fact = chosen.categories.get(category)
            if fact is None or fact.minutes is None:
                lines.append(f"До {NEAREST_LABELS[category]} от выбранной точки время неизвестно.")
                continue
            name = f" ({fact.destination})" if fact.destination else ""
            lines.append(
                f"До {NEAREST_LABELS[category]}{name} — {fmt_minutes(fact.minutes)} {fact.route_mode or facts.mode_label}."
            )
            if fact.time_id:
                metric_ids.append(fact.time_id)
        return Answer(" ".join(lines), metric_ids=metric_ids, score_ids=score_ids)
    lines = []
    for category in categories:
        summary = facts.summaries[category]
        if summary.median_minutes is None:
            lines.append(f"Время до {NEAREST_LABELS[category]} в этом расчёте неизвестно.")
            continue
        text = f"До {NEAREST_LABELS[category]} {facts.mode_label} обычно {fmt_minutes(summary.median_minutes)}"
        if summary.points > 1 and summary.best_minutes != summary.worst_minutes:
            text += (
                f": от {fmt_minutes(summary.best_minutes)} до {fmt_minutes(summary.worst_minutes)}"
                " в зависимости от места"
            )
        lines.append(text + ".")
    if len(facts.points) > 1:
        lines.append("Выберите точку на карте — скажу точно для неё.")
    return Answer(" ".join(lines))


def _access(facts: Facts, categories: list[str]) -> Answer | None:
    categories = [c for c in categories if c in facts.summaries]
    if not categories:
        return None
    if facts.selected is not None:
        parts = []
        score_ids = []
        for category in categories:
            fact = facts.selected.categories.get(category)
            if fact is None:
                continue
            parts.append(
                f"{CATEGORY_LABELS[category].capitalize()}: индекс {fmt_index(fact.index)} из 100 "
                f"({band(fact.index)}), до ближайшего объекта {fmt_minutes(fact.minutes)}."
            )
            if fact.score_id:
                score_ids.append(fact.score_id)
        if parts:
            return Answer("Для выбранной точки. " + " ".join(parts), score_ids=score_ids)
    parts = []
    for category in categories:
        summary = facts.summaries[category]
        parts.append(
            f"{summary.label.capitalize()} доступны {_adverb(summary.index)}: индекс "
            f"{fmt_index(summary.index)} из 100, до ближайшего обычно "
            f"{fmt_minutes(summary.median_minutes)} {facts.mode_label}."
        )
    return Answer(" ".join(parts))


def _adverb(value: float | None) -> str:
    return {
        "отличная": "отлично",
        "хорошая": "хорошо",
        "средняя": "средне",
        "низкая": "плохо",
        "очень низкая": "очень плохо",
    }.get(band(value), "неизвестно как")


def _points(facts: Facts, worst: bool) -> Answer | None:
    ranked = facts.ranked_points()
    if len(ranked) < 2:
        return None
    if worst:
        ranked = list(reversed(ranked))
    top = ranked[0]
    word = "Хуже всего" if worst else "Лучше всего"
    text = f"{word} доступность у точки «{top.label}»: индекс {fmt_index(top.index)} из 100."
    weak = [f for f in top.categories.values() if f.minutes is not None]
    if weak:
        item = (
            max(weak, key=lambda f: f.minutes or 0)
            if worst
            else min(weak, key=lambda f: f.minutes or 0)
        )
        text += f" Например, до {NEAREST_LABELS.get(item.category, item.label)} — {fmt_minutes(item.minutes)}."
    text += " Нажмите на неё в ответе, чтобы увидеть на карте."
    return Answer(text, score_ids=[top.score_id] if top.score_id else [])


def _improve(facts: Facts) -> Answer:
    strong, weak = _strong_weak(facts)
    if weak:
        summary = facts.summaries[weak]
        text = (
            f"Главная проблема — {summary.label}: индекс {fmt_index(summary.index)}, "
            f"обычно {fmt_minutes(summary.median_minutes)} до ближайшего объекта."
        )
        text += " Сильнее всего индекс поднимут новые объекты этой категории или транспорт к ним в точках с худшим временем."
        return Answer(text)
    ranked = facts.ranked_points()
    if ranked:
        worst = ranked[-1]
        return Answer(
            f"Хуже всего у точки «{worst.label}» (индекс {fmt_index(worst.index)}). "
            "Индекс вырастет, если сократить время до ближайших объектов именно там.",
            score_ids=[worst.score_id] if worst.score_id else [],
        )
    return _overall(facts)


def method(facts: Facts) -> Answer:
    h = f" Для этого города H = {fmt_minutes(facts.h_minutes)}." if facts.h_minutes else ""
    return Answer(
        "Индекс — оценка от 0 до 100: чем быстрее можно добраться до ближайшего объекта, тем выше. "
        "Если дорога занимает опорное время города H, индекс 50; вдвое дольше — 25; сразу рядом — 100."
        + h
        + " Общий индекс точки — среднее по выбранным категориям с учётом приоритета, "
        "индекс территории — среднее по точкам с учётом числа жителей."
    )


def hello(facts: Facts) -> Answer:
    examples = ["«Какой район самый доступный?»", "«Сколько идти до ближайшей школы?»"]
    if facts.summaries:
        first = next(iter(facts.summaries.values()))
        examples.append(f"«Насколько доступны {first.label}?»")
    return Answer(
        "Я консультант Waydex: отвечаю по посчитанным индексам доступности. Спросите, например, "
        + ", ".join(examples)
        + ". Можно выбрать точку на карте и спросить про неё."
    )


def answer(
    question: str,
    facts: Facts,
    catalog: list[DistrictFact],
    address: object | None = None,
    district: DistrictFact | None = None,
) -> Answer:
    q = " " + norm(question) + " "
    categories = categories_in(q)
    worst = has(q, *WORST)
    if has(q, *HELLO) and len(q) < 60:
        return hello(facts)
    if has(q, *METHOD) and has(q, "индекс", "оценк", "балл", "доступност", "h "):
        return method(facts)
    lookup = catalog and (has(q, *DISTRICT) or not (categories or has(q, *TIME)))
    named = find_district(q, catalog) if lookup else None
    if named is not None and (district is None or named.district_id != district.district_id):
        return _one_district(named, q)
    if (
        catalog
        and has(q, *DISTRICT)
        and (has(q, *BEST) or worst or has(q, "какой", "какие", "где"))
    ):
        found = _district_answer(q, facts, catalog, worst)
        if found is not None:
            return found
    if address is not None:
        return _address_answer(address, q)
    if district is not None:
        return _one_district(district, q)
    if facts.kind == "address":
        return Answer(
            "Найдите адрес и нажмите «Рассчитать пешком» — тогда скажу, сколько идти до больницы, "
            "школы или метро. Ещё можно спросить, какой район Москвы самый доступный."
        )
    if facts.kind == "atlas":
        return Answer(
            "Выберите территорию и нажмите «Рассчитать» — тогда отвечу по индексам и времени до "
            "объектов. Уже сейчас могу сказать, какие районы Москвы самые доступные пешком."
        )
    if has(q, *IMPROVE):
        return _improve(facts)
    if categories and has(q, *TIME):
        found = _time(facts, categories)
        if found is not None:
            return found
    if categories:
        found = _access(facts, categories) or _time(facts, categories)
        if found is not None:
            return found
        return Answer(
            "В этом расчёте нет категории "
            + ", ".join(CATEGORY_LABELS.get(c, c) for c in categories)
            + ". Добавьте её в фильтрах и пересчитайте — тогда отвечу."
        )
    if has(q, *TIME) and len(facts.summaries) == 1:
        found = _time(facts, list(facts.summaries))
        if found is not None:
            return found
    if has(q, *BEST) or worst:
        if has(q, *DISTRICT) and not catalog:
            if facts.area:
                return Answer(
                    f"Сейчас посчитан один район — {facts.area}. Чтобы сравнить районы, "
                    "посчитайте другие районы или весь город."
                )
        found = _points(facts, worst)
        if found is not None:
            return found
    if facts.selected is not None:
        point = facts.selected
        parts = [
            f"{fact.label} — {fmt_minutes(fact.minutes)}"
            for fact in point.categories.values()
            if fact.minutes is not None
        ]
        text = f"У выбранной точки индекс {fmt_index(point.index)} из 100 ({band(point.index)})."
        if parts:
            text += f" До ближайших объектов {facts.mode_label}: " + ", ".join(parts) + "."
        return Answer(text, score_ids=[point.score_id] if point.score_id else [])
    return _overall(facts)


def _address_answer(result: object, q: str) -> Answer:
    from a2_api.adapters.address_access import AddressResult

    assert isinstance(result, AddressResult)
    wanted = address_categories_in(q)
    items = [i for i in result.categories if not wanted or i.category in wanted]
    if wanted and not items:
        names = ", ".join(ADDRESS_LABELS.get(c, c) for c in wanted)
        return Answer(
            f"Для этого адреса не посчитана категория: {names}. Отметьте её и пересчитайте."
        )
    if not has(q, *TIME) and (has(q, *ACCESS) or has(q, *INDEX)):
        parts = [
            f"{ADDRESS_LABELS.get(i.category, i.category)} — "
            + ("нет маршрута" if i.index is None else f"{whole(i.index)} ({band(i.index)})")
            for i in items
        ]
        text = f"{result.address.label}: " + ", ".join(parts) + "."
        if wanted:
            lines = []
            for i in items:
                label = ADDRESS_LABELS.get(i.category, i.category).capitalize()
                if i.index is None or i.seconds is None:
                    lines.append(f"{label}: пешего маршрута поблизости не нашлось.")
                    continue
                name = f" ({i.destination.name})" if i.destination is not None else ""
                lines.append(
                    f"{label}: индекс {whole(i.index)} из 100, {band(i.index)} доступность; "
                    f"ближайший объект{name} — {fmt_minutes(i.seconds / 60)} пешком."
                )
            text = " ".join(lines)
        if not wanted and result.index is not None:
            text = (
                f"Общий индекс адреса {result.address.label} — {whole(result.index)} из 100, "
                f"{band(result.index)} доступность. По категориям: " + ", ".join(parts) + "."
            )
        elif not wanted:
            missing = [ADDRESS_LABELS.get(i.category, i.category) for i in items if i.index is None]
            if missing:
                text += (
                    " Общий индекс не посчитан: нет пешего маршрута до категории "
                    + ", ".join(missing)
                    + ". Снимите её и пересчитайте."
                )
        return Answer(text)
    lines = []
    for item in items:
        if item.seconds is None:
            lines.append(
                f"до {ADDRESS_NEAREST.get(item.category, item.category)} пешего маршрута "
                "поблизости не нашлось"
            )
            continue
        name = f" ({item.destination.name})" if item.destination is not None else ""
        lines.append(
            f"до {ADDRESS_NEAREST.get(item.category, item.category)}{name} — "
            f"{fmt_minutes(item.seconds / 60)} пешком"
        )
    text = f"От адреса {result.address.label}: " + "; ".join(lines) + "."
    if not wanted and result.index is not None:
        text += f" Общий индекс адреса — {whole(result.index)} из 100."
    return Answer(text)
