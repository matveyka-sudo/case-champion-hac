"""Waydex consultant: short answers from saved indices, districts and a model."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from a2_api.assistant.consultant import Consultant
from a2_api.assistant.consultant.catalog import address_query
from a2_api.assistant.explanations import explain
from a2_contracts.v2.assistant import ExplanationRequest, ExplanationSelection
from a2_contracts.v2.results import AnalysisResult

ROOT = Path(__file__).resolve().parents[3]
TIMES = {"school": [6, 9, 14, 22, 35], "adult_polyclinic": [11, 18, 25, 31, 48]}


def saved() -> AnalysisResult:
    raw = json.loads(
        (ROOT / "packages/contracts/fixtures/v2/urban_passenger-result.json").read_text()
    )["payload"]
    base = AnalysisResult.model_validate(raw)
    metric, score = base.metrics[0], base.scores[0]
    city = base.request.city_id
    metrics, scores = [], []
    for number in range(5):
        point = f"p{number + 1}"
        subject = metric.subject.model_copy(
            update={"kind": "sample_point", "point_id": point, "category": None, "mode": "walk"}
        )
        indices = []
        for category, times in TIMES.items():
            minutes = times[number]
            item = subject.model_copy(update={"category": category})
            metrics.append(
                metric.model_copy(
                    update={
                        "metric_id": f"time-{point}-{category}",
                        "name": "minimum_journey_seconds",
                        "value": minutes * 60.0,
                        "subject": item,
                    }
                )
            )
            index = 100 * 2 ** (-minutes / 40)
            indices.append(index)
            scores.append(
                score.model_copy(
                    update={
                        "score_id": f"category-{point}-{category}",
                        "name": "category_index",
                        "value": index,
                        "subject": item,
                    }
                )
            )
        scores.append(
            score.model_copy(
                update={
                    "score_id": f"point-{point}",
                    "name": "point_index",
                    "value": sum(indices) / 2,
                    "subject": subject,
                }
            )
        )
    territory = score.subject.model_copy(
        update={
            "kind": "territory",
            "territory_id": city,
            "point_id": None,
            "category": None,
            "mode": "walk",
        }
    )
    scores.append(
        score.model_copy(
            update={
                "score_id": "territory-walk",
                "name": "territory_index",
                "value": 61.4,
                "subject": territory,
            }
        )
    )
    return base.model_copy(update={"metrics": metrics, "scores": scores})


def service_for(result: AnalysisResult, city: str = "Москва") -> SimpleNamespace:
    territory = SimpleNamespace(territory_id=result.request.city_id, name=city)
    registry = SimpleNamespace(
        snapshots={result.snapshot_id: SimpleNamespace(territories=[territory])}, areas={}
    )
    return SimpleNamespace(registry=registry)


@pytest.fixture
def districts(monkeypatch, tmp_path):
    reports = {
        "moscow-relation-1": {
            "district": {"id": "moscow-relation-1", "name": "район Арбат"},
            "index": 82.5,
            "categories": {"school": 93.7, "metro": 90.5, "mck": 56.2},
            "population": 32000,
        },
        "moscow-relation-2": {
            "district": {"id": "moscow-relation-2", "name": "район Вороново"},
            "index": 25.1,
            "categories": {"school": 40.0, "metro": 5.0, "mck": 3.0},
            "population": 9000,
        },
        "moscow-relation-3": {
            "district": {"id": "moscow-relation-3", "name": "Тверской район"},
            "index": None,
            "categories": {"school": 98.0, "metro": 92.0, "mck": 41.0},
            "population": 98000,
        },
        "oblast-ru-1": {
            "district": {"id": "oblast-ru-1", "name": "городской округ Балашиха"},
            "index": 77.0,
            "categories": {"school": 90.0},
            "population": 500000,
            "sample_count": 120,
        },
    }
    (tmp_path / "districts.json").write_text(json.dumps(reports, ensure_ascii=False))
    monkeypatch.setenv("A2_ADDRESS_CATALOGUE", str(tmp_path / "catalog.sqlite"))


@pytest.fixture
def no_districts(monkeypatch, tmp_path):
    monkeypatch.setenv("A2_ADDRESS_CATALOGUE", str(tmp_path / "missing.sqlite"))


async def ask(question, *, selection=None, model=None, city="Москва"):
    result = saved()
    return await Consultant(model).explain(
        service_for(result, city),
        result,
        ExplanationRequest(question=question, selection=selection),
        "request-1",
    )


def text(answer) -> str:
    return " ".join(finding.text for finding in answer.findings)


@pytest.mark.asyncio
async def test_time_to_the_nearest_object_from_the_selected_point(no_districts):
    answer = await ask(
        "Сколько идти до ближайшей больницы?",
        selection=ExplanationSelection(entity_kind="sample_point", entity_id="p4"),
    )
    assert answer.status == "template" and answer.reason_code == "DISABLED"
    assert text(answer) == "До ближайшей взрослой поликлиники — 31 мин пешком."
    assert answer.findings[0].metric_ids == ["time-p4-adult_polyclinic"]
    assert answer.analysis_result_hash == saved().result_hash


@pytest.mark.asyncio
async def test_without_a_point_the_typical_time_and_a_hint(no_districts):
    answer = text(await ask("Сколько идти до школы?"))
    assert answer.startswith("До ближайшей школы пешком обычно 14 мин: от 6 мин до 35 мин")
    assert "Выберите точку на карте" in answer


@pytest.mark.asyncio
async def test_category_access_and_overall_index_are_short(no_districts):
    assert text(await ask("Насколько доступны школы?")) == (
        "Школы доступны хорошо: индекс 75 из 100, до ближайшего обычно 14 мин пешком."
    )
    overall = await ask("Какой общий индекс?")
    assert text(overall).startswith("Общий индекс доступности (Москва, пешком) — 61 из 100")
    assert "со школами" in text(overall) and "со взрослыми поликлиниками" in text(overall)
    assert overall.findings[0].score_ids == ["territory-walk"]


@pytest.mark.asyncio
async def test_a_category_outside_the_calculation_is_named(no_districts):
    answer = text(await ask("Сколько ехать до аэропорта?"))
    assert answer.startswith("В этом расчёте нет категории аэропорты")


@pytest.mark.asyncio
async def test_worst_point_is_linked_for_the_map(no_districts):
    answer = await ask("Где хуже всего доступность?")
    assert "точка 5" in text(answer) and answer.findings[0].score_ids == ["point-p5"]


@pytest.mark.asyncio
async def test_district_ranking_and_one_district(districts):
    best = text(await ask("Какой район наиболее транспортно доступный?"))
    assert best.startswith(
        "Лучше всего — район Арбат: индекс 83 из 100. Следом Тверской район (77) и район Вороново"
    )
    worst = text(await ask("Какой район самый недоступный?"))
    assert worst.startswith("Хуже всего — район Вороново: индекс 25 из 100.")
    schools = text(await ask("Какой район лучше всего со школами?"))
    assert schools.startswith("Лучше всего со школами — Тверской район: индекс 98")
    assert text(await ask("Какой индекс у Арбата?")).startswith(
        "Район Арбат: индекс 83 из 100, отличная пешая доступность."
    )
    assert text(await ask("Как в Тверском районе с метро?")) == (
        "Тверской район: метро — 92 из 100, отличная пешая доступность."
    )
    oblast = text(await ask("Какой городской округ в области самый доступный?"))
    assert oblast.startswith("Лучше всего — городской округ Балашиха")


@pytest.mark.asyncio
async def test_other_cities_do_not_get_the_moscow_district_table(districts):
    answer = text(await ask("Какой район самый доступный?", city="Калининград"))
    assert "Арбат" not in answer and "Тверской" not in answer


class Model:
    model_id = "aliceai-llm-flash"

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def ask(self, question, context, draft):
        self.calls.append((question, context, draft))
        return self.reply


@pytest.mark.asyncio
async def test_model_answer_is_generated_with_the_facts_and_the_draft(districts):
    model = Model("Лучше всего — **район Арбат** [territory-walk]: индекс 83.")
    answer = await ask("Какой район самый доступный?", model=model)
    assert answer.status == "generated" and answer.model_id == "aliceai-llm-flash"
    assert answer.reason_code is None
    assert text(answer) == "Лучше всего — район Арбат: индекс 83."
    question, context, draft = model.calls[0]
    assert "Тверской район: 77" in context and "Общий индекс территории: 61" in context
    assert draft.startswith("Лучше всего — район Арбат")


@pytest.mark.asyncio
async def test_model_failure_returns_the_direct_answer(no_districts):
    answer = await ask("Насколько доступны школы?", model=Model(None))
    assert answer.status == "template" and answer.reason_code == "PROVIDER_UNAVAILABLE"
    assert text(answer).startswith("Школы доступны хорошо")


@pytest.mark.asyncio
async def test_explanation_endpoint_logic_uses_the_consultant(no_districts):
    result = saved()
    service = service_for(result)
    service.analysis = AsyncMock(return_value=result)
    service.consultant = Consultant()
    answer = await explain(
        service, result.analysis_id, "scope", "request-2", ExplanationRequest(question="Привет")
    )
    assert text(answer).startswith("Я консультант Waydex")
    assert answer.findings[0].metric_ids == [] and answer.findings[0].score_ids == []


def test_address_is_found_in_a_question():
    assert address_query("сколько идти до школы от ул. Тверская, д. 7?") == "тверская 7"
    assert address_query("Сколько идти до больницы от Ленинский проспект 32а") == (
        "ленинский проспект 32а"
    )
    assert address_query("Какой район лучше за 15 минут?") is None


def address_result():
    from a2_api.adapters.address_access import AddressResult

    def place(name):
        return {
            "id": name,
            "name": name,
            "lat": 0,
            "lon": 0,
            "location": "mapped_entrance",
            "line": "",
        }

    return AddressResult.model_validate(
        {
            "address": {
                "id": "way/1",
                "label": "Тверская улица, 12",
                "region": "Москва",
                "lat": 55.76,
                "lon": 37.6,
                "h_seconds": 2400,
            },
            "index": 84.2,
            "source": "osm",
            "categories": [
                {
                    "category": "hospital",
                    "seconds": 720,
                    "index": 81.0,
                    "destination": place("ГКБ №1"),
                },
                {
                    "category": "school",
                    "seconds": 300,
                    "index": 91.7,
                    "destination": place("Школа №1239"),
                },
            ],
        }
    )


@pytest.mark.asyncio
async def test_address_page_questions(districts):
    from a2_api.assistant.consultant.service import ConsultantQuestion

    consultant = Consultant()

    async def answer(question, **context):
        return (await consultant.ask(ConsultantQuestion(question=question, **context))).text

    address = address_result()
    assert await answer("Сколько идти до ближайшей больницы?", address=address) == (
        "От адреса Тверская улица, 12: до ближайшей больницы (ГКБ №1) — 12 мин пешком."
    )
    assert await answer("Насколько доступны школы?", address=address) == (
        "Школа: индекс 92 из 100, отличная доступность; ближайший объект (Школа №1239) — 5 мин пешком."
    )
    assert (await answer("Сколько до детского сада?", address=address)).startswith(
        "Для этого адреса не посчитана категория: детский сад."
    )
    assert (await answer("Какой район самый доступный?", address=address)).startswith(
        "Лучше всего — район Арбат"
    )
    assert (await answer("Сколько идти до больницы?")).startswith("Найдите адрес")
    assert (await answer("Сколько идти до больницы?", city="Москва")).startswith(
        "Выберите территорию и нажмите «Рассчитать»"
    )


@pytest.mark.asyncio
async def test_consultant_ask_endpoint(districts):
    import httpx
    from a2_api.api.application import create_app
    from a2_api.services.analyses import AnalysisService

    service = AnalysisService()
    service.consultant = Consultant()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/v1/consultant/ask", json={"question": "Какой район самый недоступный?"}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "template" and body["text"].startswith("Хуже всего — район Вороново")
