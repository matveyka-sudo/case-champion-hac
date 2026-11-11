"""Bounded qualitative generation with server-resolved facts and local provenance.

The lexical checks are conservative output guards, not an entailment proof for
arbitrary Russian prose. Causes and forecasts still require source evidence;
the model is instructed to explain their absence rather than invent them.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

from a2_contracts.v2.assistant import Explanation, Finding
from a2_contracts.v2.evidence import EvidenceResolver
from a2_contracts.v2.results import AnalysisResult, Metric, Score
from pydantic import PrivateAttr

from .narrative import (
    _comparison,
    _coverage,
    _journey,
    _methodology,
    _safety,
    conversational_finding,
)

_PROSE_RULES = frozenset(
    {
        "evidence_mismatch",
        "placeholder_mismatch",
        "placeholder_repeated",
        "placeholder_context",
        "atom_limit",
        "paragraph_budget",
        "qualitative_budget",
        "literal_number",
        "written_number",
        "markup",
        "control_character",
        "causal_assertion",
        "coverage_assertion",
        "forecast_assertion",
        "comparative_assertion",
        "no_route_assertion",
        "unsupported_term",
        "assembled_text_too_long",
    }
)


class ProseRejected(ValueError):
    """A fixed rule identifier, never text supplied by the model or a source."""

    def __init__(self, rule: str) -> None:
        self.rule = rule if rule in _PROSE_RULES else "other"
        super().__init__(self.rule)


@dataclass(frozen=True)
class ProseLimits:
    paragraph_max_characters: int
    qualitative_max_characters: int
    max_optional_atoms: int = 1
    max_occurrences_per_placeholder: int = 1
    final_text_max_characters: int = 1000


def prose_limits(saved: AnalysisResult, facts: list[Metric | Score]) -> ProseLimits:
    """Budget every allowed subset of up to three facts without expanding prose.

    Coverage/comparison depend on at most a pair of states; methodology only on
    the analysis and mode. A journey atom can describe one linked journey, or
    the fixed no-unique-journey clause. Safety over all exposed facts is a
    superset of the warnings for every selected subset.
    """
    singles = [[fact] for fact in facts]
    pairs = [list(pair) for pair in combinations(facts, 2)]
    state_sets = [[], facts, *singles, *pairs]
    atom_max = max(
        [len(_methodology(saved, subset)) for subset in [facts, *singles]]
        + [len(_coverage(subset)) for subset in state_sets]
        + [len(_comparison(subset)) for subset in state_sets]
        + [len(_journey(saved, subset)) for subset in [[], *singles]]
    )
    values_max = sum(sorted((len(_display(fact)) for fact in facts), reverse=True)[:3])
    # One separating space is inserted before the complete server safety text.
    budget = 1000 - atom_max - values_max - len(_safety(saved, facts)) - 1
    return ProseLimits(650, min(650, budget))


def fact_placeholder(fact: Metric | Score) -> str:
    if isinstance(fact, Metric):
        return "[[metric:" + fact.metric_id + "]]"
    return "[[score:" + fact.score_id + "]]"


def _display(fact: Metric | Score) -> str:
    if fact.value is None:
        return "нет данных"
    number = str(int(fact.value)) if fact.value.is_integer() else repr(fact.value)
    return number + (" " + fact.unit if isinstance(fact, Metric) else " из 100")


# The question itself is never filtered. These checks apply only to generated
# prose after removing declared fact placeholders, before server substitutions.
_NUMERAL = re.compile(
    r"\b(?:нол\w*|нул\w*|один|одна|одно|одну|одного|одной|одному|одним|одном|одних|одни|"
    r"два|две|двух|двум\w*|двест\w*|двухсот\w*|двухст\w*|"
    r"три|трех|трем\w*|тремя\w*|трист\w*|трехсот\w*|четыр\w*|пят\w*|шест\w*|семь\w*|семи\w*|"
    r"восем\w*|восьм\w*|девят\w*|десят\w*|одиннадцат\w*|двенадцат\w*|тринадцат\w*|"
    r"четырнадцат\w*|пятнадцат\w*|шестнадцат\w*|семнадцат\w*|восемнадцат\w*|"
    r"девятнадцат\w*|двадцат\w*|тридцат\w*|сорок\w*|пятьдесят\w*|шестьдесят\w*|"
    r"семьдесят\w*|восемьдесят\w*|девяност\w*|сто|ста|сот\w*|тысяч\w*|миллион\w*|"
    r"миллиард\w*|триллион\w*|полтор\w*|дюжин\w*|вдвое|втрое|вчетверо|впятеро|"
    r"дважды|трижды|половин\w*|стопроцент\w*|zero|one|two|three|four|five|six|seven|"
    r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|billion|half|double|triple)\b",
    re.IGNORECASE,
)
_CAUSAL = re.compile(
    r"из-за|вследствие|потому что|по причин\w*|обусловлен\w*|вызван\w*|"
    r"причин\w*\s+(?:\w+\s+){0,5}(?:—|-|:|явля\w*|это\b)|"
    r"причин\w*\s*(?:—|-|:)|привел\w* к|повлек\w*|поэтому",
    re.IGNORECASE,
)
_PROMOTION = re.compile(
    r"данные (?:полны\w*|полные|исчерпывающ\w*)|"
    r"(?:полн\w*|исчерпывающ\w*) данные|полн\w* (?:охват|покрыти\w*)|"
    r"полнота (?:доказана|подтверждена)|полностью подтвержд\w*|"
    r"точн\w* индекс|каноническ\w* индекс|доказан\w* минимум\w*|"
    r"минимум (?:доказан|подтвержд\w*)|гарантир\w*|\bрейтинг\w*|"
    r"минимальн\w* время (?:доказан\w*|подтвержд\w*)|"
    r"все (?:маршрут\w*|объект\w*|пут\w*)|единственн\w*|ближайш\w*|"
    r"реальн\w* (?:оценк\w*|индекс\w*|город\w*)|"
    r"(?:является|заменяет) индексом категории",
    re.IGNORECASE,
)
_FORECAST = re.compile(
    r"(?:время|ожидание|путь) (?:станет|сократится|уменьшится|увеличится)|"
    r"(?:сценарий|изменение|пересадка|запуск|строительство|добавление|согласование)"
    r".{0,50}(?:сократ\w*|улучш\w*|сниз\w*|повыс\w*|обеспеч\w*|устран\w*|даст)|"
    r"(?:запустил|запустила|запускаю|выполнил|выполнила|выполняю) сценарий|"
    r"(?:будет|станет) (?:удобн\w*|быстр\w*|доступн\w*|лучш\w*|хуж\w*)",
    re.IGNORECASE,
)
_ORDER = re.compile(
    r"\b(?:выше|ниже|лучше|хуже|равны|равен|равна|быстрее|медленнее|длиннее|короче|больше|меньше)\b|"
    r"(?:индекс|оценка)\s+(?:очень\s+)?(?:низк\w*|высок\w*|слаб\w*|сильн\w*)|"
    r"(?:сам\w*|более|менее)\s+(?:низк\w*|высок\w*|быстр\w*|доступн\w*)",
    re.IGNORECASE,
)
_ABSENT = re.compile(
    r"маршрут\w* (?:нет|отсутству\w*|не существует)|"
    r"(?:добраться|доехать|проехать) (?:невозможно|нельзя)|связи нет|"
    r"транспорт недоступен|(?:нет|отсутству\w*|не существует)\s+"
    r"(?:(?:подходящ\w*|доступн\w*|прям\w*|никак\w*)\s+)?"
    r"(?:маршрут\w*|транспорт\w*|связ\w*)",
    re.IGNORECASE,
)


def _locally_limited(prefix: str) -> bool:
    # A negation can govern a restricted phrase, never a whole paragraph. The
    # caller splits independent clauses first. Unknown tariffs, for example,
    # cannot excuse a later assertion that every route is covered.
    return bool(
        re.search(r"\b(?:не|нет|без)\s+$", prefix)
        or re.search(
            r"\b(?:нельзя|не (?:могу|можно|следует|позволяет|означает|является))\s+"
            r"(?:(?:говорить|утверждать|считать|обещать|гарантировать|подтвердить|доказать|установить|"
            r"определить|сравнивать|объяснять|заключить|сделать вывод)\s+)?"
            r"(?:(?:что|это|о|об|как)\s+)?$",
            prefix,
        )
    )


def _check_qualitative(text: str) -> None:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    if any(char.isnumeric() for char in text + normalized):
        raise ProseRejected("literal_number")
    if _NUMERAL.search(normalized) or re.search(r"\b[ivxlcdm]+\b", normalized):
        raise ProseRejected("written_number")
    if re.search(
        r"(?:https?|ftp)://|www\.|[<>{}=+×÷%]|\[\[|\]\]|"
        r"\b[\w-]+\.(?:[a-z]{2,}|рф)\b|\b[\w.+-]+@",
        normalized,
    ):
        raise ProseRejected("markup")
    if any(unicodedata.category(char) in {"Cc", "Cf"} and char not in "\n\t" for char in text):
        raise ProseRejected("control_character")
    for clause in re.split(r"[,.!?;\n]+|\s+(?:но|однако|зато|и|а)\s+", normalized):
        for pattern, rule in (
            (_CAUSAL, "causal_assertion"),
            (_PROMOTION, "coverage_assertion"),
            (_FORECAST, "forecast_assertion"),
            (_ORDER, "comparative_assertion"),
            (_ABSENT, "no_route_assertion"),
        ):
            for match in pattern.finditer(clause):
                if not _locally_limited(clause[: match.start()]):
                    raise ProseRejected(rule)


# Closed vocabulary for model prose. A content word must come from this domain
# lexicon or from the saved text the answer is about (fact names, units, saved
# limitations, server atoms and warnings). Any other word may appear only in a
# clause that states the limit of the saved data ("по этим данным нельзя
# установить ...", "данные не описывают ..."), never in a plain assertion:
# "дорожное покрытие разрушено" has no saved support. A plain negation is not
# enough, since "покрытие не отремонтировано" is an assertion too.
_FUNCTION_WORDS = frozenset(
    """а и или но да же ли бы не ни нет без в во на по о об обо от до из к ко с со у за
    над под при про для через между перед после как так что чтобы это эта этот эти этого
    этой этом этим этих тот та те то того той тем там тут здесь где когда если уже еще
    только лишь также тоже даже именно ее его их им ей ему они она он оно мы вы ты я вас
    нас вам нам меня мне вам себя свой своя свое свои своих своим своей который которая
    которое которые которого которой котором которым которых каждый каждая каждое каждого
    все весь вся всех всего всей всем сам сама само сами самого самой может могут можно
    нельзя нужно надо следует есть был была было были будет быть пока сейчас вот какой
    какая какое какие каких такой такая такое такие таких такого этому иначе либо ведь
    вообще""".split()
)
_LEXICON = (
    "сохран",
    "расчет",
    "рассчит",
    "посчит",
    "показат",
    "значен",
    "данн",
    "сведен",
    "информац",
    "времени",
    "время",
    "минут",
    "секунд",
    "часов",
    "маршрут",
    "путь",
    "пути",
    "поездк",
    "пересад",
    "ожидан",
    "ожида",
    "отправлен",
    "рейс",
    "остановк",
    "пешк",
    "пешеход",
    "транспорт",
    "автомоб",
    "машин",
    "общественн",
    "индекс",
    "оценк",
    "балл",
    "категор",
    "объект",
    "точк",
    "город",
    "территор",
    "район",
    "участ",
    "модел",
    "модельн",
    "полнот",
    "огранич",
    "метод",
    "формул",
    "норматив",
    "сценари",
    "предлож",
    "вариант",
    "выбран",
    "выбор",
    "текущ",
    "доступ",
    "недоступ",
    "известн",
    "неизвестн",
    "предварит",
    "отдельн",
    "провер",
    "подтвер",
    "неподтвер",
    "учит",
    "учет",
    "относ",
    "означа",
    "показыва",
    "указыва",
    "завис",
    "содерж",
    "включа",
    "описыва",
    "объясн",
    "ответ",
    "вопрос",
    "услов",
    "параметр",
    "результат",
    "анализ",
    "запрос",
    "источник",
    "документ",
    "ссылк",
    "основан",
    "оценива",
    "измер",
    "величин",
    "единиц",
    "площад",
    "класс",
    "длитель",
    "расстоян",
    "километр",
    "метр",
    "поликлин",
    "взросл",
    "детск",
    "школ",
    "колледж",
    "универс",
    "институт",
    "вокзал",
    "автовокзал",
    "аэропорт",
    "торгов",
    "центр",
    "груз",
    "площадк",
    "узел",
    "узл",
    "терминал",
    "порт",
    "станц",
    "трубопровод",
    "направлен",
    "межгород",
    "связ",
    "сравн",
    "сопостав",
    "разниц",
    "отлича",
    "различ",
    "примен",
    "непримен",
    "соответств",
    "конкрет",
    "подробн",
    "кратк",
    "общ",
    "част",
    "нужн",
    "необход",
    "получ",
    "найд",
    "нашл",
    "явля",
    "имеет",
    "имеют",
    "позвол",
    "сначал",
    "затем",
    "далее",
    "итог",
    "например",
    "кроме",
    "однак",
    "друг",
    "ин",
    "нов",
    "прям",
    "указан",
    "сделан",
    "выполн",
    "отсутств",
    "недостат",
    "достаточ",
    "уточн",
    "попроб",
    "посмотр",
    "откр",
    "карт",
    "выбер",
    "выбра",
    "смысл",
    "суть",
    "важн",
    "главн",
    "основн",
    "реальн",
    "физическ",
    "синтетич",
    "демонстрац",
    "наблюд",
    "свед",
    "покрыти",
    "охват",
    "состоян",
    "причин",
    "фактор",
    "эффект",
    "изменен",
    "улучшен",
    "работ",
    "использ",
    "помог",
    "помощ",
    "мож",
    "должн",
    "стоит",
    "дает",
    "даёт",
    "будут",
    "был",
    "есть",
    "нет",
    "здесь",
    "такж",
    "лишь",
    "пока",
    "сейчас",
    "чтени",
    "начн",
    "начин",
    "разбер",
    "установ",
    "полезн",
    "раздел",
    "отдел",
    "числен",
    "мест",
    "требу",
    "поня",
    "запис",
    "фактич",
    "утвержд",
    "полн",
    "гарантир",
    "могу",
    "говор",
    "счита",
    "обещ",
    "доказ",
    "определ",
    "заключ",
    "вывод",
    "опира",
    "смотр",
    "качеств",
    "действит",
    "именно",
    "конечн",
    "стоимост",
)
_EPISTEMIC = re.compile(
    r"\b(?:нельзя|невозможно|неизвестн\w*)"
    r"|\bне\s+(?:могу|можем|удается|получится|описыва\w*|содерж\w*|отража\w*|сообща\w*|"
    r"включа\w*|учитыва\w*|позволя\w*|показыва\w*)"
    r"|\b(?:нет|без|недостаточно|отсутству\w*)\s+(?:\w+\s+)?(?:данн|сведен|информац|основан)\w*"
)
_WORD = re.compile(r"[a-zа-я]+")


def _words(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return _WORD.findall(normalized)


def _same_stem(a: str, b: str) -> bool:
    common = 0
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        common += 1
    return common >= 5 or (common == min(len(a), len(b)) and common >= 4)


def _known(word: str, vocabulary: set[str]) -> bool:
    return (
        len(word) <= 3
        or word in _FUNCTION_WORDS
        or any(word.startswith(stem) for stem in _LEXICON)
        or any(_same_stem(word, saved) for saved in vocabulary)
    )


def _check_grounding(text: str, saved_text: str) -> None:
    vocabulary = {word for word in _words(saved_text) if len(word) >= 4}
    normalized = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    for clause in re.split(r"[,.!?;:\n—()]+", normalized):
        unknown = [word for word in _WORD.findall(clause) if not _known(word, vocabulary)]
        if unknown and not _EPISTEMIC.search(clause):
            raise ProseRejected("unsupported_term")


def generated_finding(
    paragraph: str,
    saved: AnalysisResult,
    metric_ids: list[str],
    score_ids: list[str],
    evidence_ids: list[str],
    *,
    has_proposal: bool,
    resolver: EvidenceResolver | None = None,
    limits: ProseLimits | None = None,
    retrieved_ids: frozenset[str] = frozenset(),
) -> Finding:
    limits = limits or prose_limits(
        saved,
        [
            *(m for m in saved.metrics if m.metric_id in metric_ids),
            *(s for s in saved.scores if s.score_id in score_ids),
        ],
    )
    if len(paragraph) > limits.paragraph_max_characters:
        raise ProseRejected("paragraph_budget")
    canonical = conversational_finding(
        "help", saved, metric_ids, score_ids, has_proposal=has_proposal, resolver=resolver
    )
    metrics, scores = {m.metric_id: m for m in saved.metrics}, {s.score_id: s for s in saved.scores}
    facts: list[Metric | Score] = [
        *(metrics[identifier] for identifier in metric_ids),
        *(scores[identifier] for identifier in score_ids),
    ]
    # A paragraph may also cite documents that search_evidence returned in this
    # explanation; their provenance is checked against the retrieved set later.
    if len(evidence_ids) != len(set(evidence_ids)) or not set(evidence_ids) <= (
        set(canonical.evidence_refs) | retrieved_ids
    ):
        raise ProseRejected("evidence_mismatch")
    cited = [*canonical.evidence_refs]
    cited += [ref for ref in evidence_ids if ref not in cited]
    replacements = {fact_placeholder(fact): _display(fact) for fact in facts}
    used = {token for token in replacements if token in paragraph}
    if used != set(replacements):
        raise ProseRejected("placeholder_mismatch")
    if any(paragraph.count(token) != 1 for token in replacements):
        raise ProseRejected("placeholder_repeated")
    atoms: dict[str, Callable[[], str]] = {
        "[[comparison]]": lambda: _comparison(facts),
        "[[coverage]]": lambda: _coverage(facts),
        "[[methodology]]": lambda: _methodology(saved, facts),
        "[[journey]]": lambda: _journey(saved, facts),
    }
    if sum(paragraph.count(token) for token in atoms) > 1:
        raise ProseRejected("atom_limit")
    if re.search(
        r"(?:\bне|\bневерно|\bложно|\bкроме|\bвместо)\s+(?:что\s+)?\[\[|"
        r"\w\[\[|\]\]\w",
        paragraph.casefold(),
    ):
        raise ProseRejected("placeholder_context")
    qualitative = paragraph
    for token in [*replacements, *atoms]:
        qualitative = qualitative.replace(token, "")
    if len(qualitative) > limits.qualitative_max_characters:
        raise ProseRejected("qualitative_budget")
    raw = paragraph
    for token in sorted([*replacements, *atoms], key=len, reverse=True):
        raw = raw.replace(token, " ")
    _check_qualitative(raw)
    saved_text = " ".join(
        [
            *(fact.name for fact in facts),
            *(fact.unit for fact in facts if isinstance(fact, Metric)),
            *saved.limitations,
            *(build() for build in atoms.values()),
            _safety(saved, facts),
        ]
    )
    _check_grounding(raw, saved_text)
    for token, value in replacements.items():
        paragraph = paragraph.replace(token, value)
    for token, build in atoms.items():
        if token in paragraph:
            paragraph = paragraph.replace(token, build())
    # Never rely on model prose to disclose these scope limits.
    paragraph = " ".join(part for part in (paragraph.strip(), _safety(saved, facts)) if part)
    if len(paragraph) > limits.final_text_max_characters:
        raise ProseRejected("assembled_text_too_long")
    return Finding.model_validate(
        canonical.model_dump() | {"text": paragraph, "evidence_refs": cited[:50]}
    )


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _source_digest(explanation: Explanation, saved: AnalysisResult) -> str:
    metric_ids = {
        identifier for finding in explanation.findings for identifier in finding.metric_ids
    }
    score_ids = {identifier for finding in explanation.findings for identifier in finding.score_ids}
    selected_metrics = [metric for metric in saved.metrics if metric.metric_id in metric_ids]
    journey_ids = {
        metric.journey_id for metric in selected_metrics if metric.journey_id is not None
    }
    return _digest(
        {
            "analysis_id": saved.analysis_id,
            "analysis_kind": saved.analysis_kind,
            "snapshot_id": saved.snapshot_id,
            "result_hash": saved.result_hash,
            "request": saved.request.model_dump(mode="json"),
            "versions": saved.versions.model_dump(mode="json"),
            "limitations": saved.limitations,
            "metrics": [metric.model_dump(mode="json") for metric in selected_metrics],
            "scores": [
                score.model_dump(mode="json")
                for score in saved.scores
                if score.score_id in score_ids
            ],
            "journeys": [
                journey.model_dump(mode="json")
                for journey in saved.journeys
                if journey.journey_id in journey_ids
            ],
            "evidence": [evidence.model_dump(mode="json") for evidence in saved.evidence_refs],
            **(
                {
                    "evidence_groups": [
                        group.model_dump(mode="json") for group in saved.evidence_groups
                    ]
                }
                if saved.evidence_groups
                else {}
            ),
        }
    )


@dataclass(frozen=True)
class _Witness:
    output_digest: str
    source_digest: str
    finding_json: str


class _GroundedExplanation(Explanation):
    # In-process provenance, never an API credential or persisted trust token.
    # Python copies keep it; public JSON deliberately cannot recreate it.
    _prose_witness: _Witness | None = PrivateAttr(default=None)


def seal_explanation(explanation: Explanation, saved: AnalysisResult) -> Explanation:
    sealed = _GroundedExplanation.model_validate(explanation.model_dump(mode="json"))
    sealed._prose_witness = _Witness(
        _digest(sealed.model_dump(mode="json")),
        _source_digest(sealed, saved),
        sealed.findings[0].model_dump_json(),
    )
    return sealed


def approved_finding(explanation: Explanation, saved: AnalysisResult) -> Finding | None:
    if not isinstance(explanation, _GroundedExplanation):
        return None
    witness = explanation._prose_witness
    if (
        witness is None
        or witness.output_digest != _digest(explanation.model_dump(mode="json"))
        or witness.source_digest != _source_digest(explanation, saved)
    ):
        raise ValueError("Generated narrative or source binding changed")
    return Finding.model_validate_json(witness.finding_json)
