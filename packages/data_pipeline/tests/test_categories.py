import pytest
from a2_data_pipeline.categories import classify, ownership_hint


@pytest.mark.parametrize(
    ("tags", "category"),
    [
        ({"amenity": "clinic", "name": "Городская поликлиника №1"}, "adult_polyclinic"),
        ({"amenity": "doctors", "name": "Детская поликлинника №5"}, "child_polyclinic"),
        ({"amenity": "hospital", "name": "Городская больница №2"}, None),
        ({"amenity": "clinic", "name": "Инвитро"}, None),
        ({"amenity": "school", "name": "МАОУ СОШ № 7"}, "school"),
        ({"amenity": "school", "name": "СДЮСШ Спортивных единоборств"}, None),
        ({"amenity": "school", "name": "Автошкола Форсаж"}, None),
        ({"amenity": "kindergarten", "name": "Детский сад 113"}, "kindergarten"),
        ({"amenity": "university", "name": "БФУ им. И. Канта"}, "college_university"),
        ({"amenity": "college", "name": "Колледж сервиса и туризма"}, "college_university"),
        ({"amenity": "bus_station", "name": "Автобусная остановка"}, None),
        ({"amenity": "bus_station", "name": "Калининградский автовокзал"}, "bus_terminal"),
        ({"building": "train_station", "name": "Южный вокзал"}, "rail_terminal"),
        ({"railway": "station", "name": "Лесное-Новое", "train": "yes"}, None),
        ({"railway": "station", "station": "subway", "name": "Вокзальная"}, None),
        ({"aeroway": "aerodrome", "name": "Девау"}, None),
        ({"aeroway": "aerodrome", "name": "Храброво", "iata": "KGD"}, "airport"),
        ({"shop": "mall", "name": "Европа"}, "shopping_center"),
        ({"shop": "supermarket", "name": "Виктория"}, None),
        ({"amenity": "school", "name": "Школа", "disused": "yes"}, None),
    ],
)
def test_classify(tags: dict[str, str], category: str | None) -> None:
    assert classify(tags)[0] == category


def test_city_named_passenger_station_is_terminal() -> None:
    tags = {
        "railway": "station",
        "name": "Калининград-Северный",
        "train": "yes",
        "public_transport": "station",
    }
    assert classify(tags)[0] is None
    assert classify(tags, "Калининград")[0] == "rail_terminal"
    freight_yard = {**tags, "name": "Калининград-Сортировочный", "public_transport": "no"}
    assert classify(freight_yard, "Калининград")[0] is None


@pytest.mark.parametrize(
    ("name", "ownership"),
    [
        ("МАОУ СОШ № 7", "municipal"),
        ("ГБУЗ КО «Городская поликлиника»", "regional"),
        ("ФГАОУ ВО БФУ им. И. Канта", "federal"),
        ("Хорошая поликлиника", "unknown"),
    ],
)
def test_ownership_hint_is_only_a_hint(name: str, ownership: str) -> None:
    assert ownership_hint({"name": name}) == ownership


def test_main_station_named_as_city_is_terminal() -> None:
    tags = {
        "railway": "station",
        "name": "Новороссийск",
        "train": "yes",
        "public_transport": "station",
    }
    assert classify(tags, "Новороссийск")[0] == "rail_terminal"
    assert classify({**tags, "name": "Новороссийск-Товарная"}, "Новороссийск")[0] == "rail_terminal"
    assert classify({**tags, "name": "Тоннельная"}, "Новороссийск")[0] is None
