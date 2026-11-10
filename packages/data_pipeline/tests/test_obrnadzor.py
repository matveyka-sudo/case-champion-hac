"""Accreditation registry rules on synthetic organisations and a synthetic XML file."""

import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from a2_data_pipeline import obrnadzor as ob

UNIVERSITY = ob.EduOrg(
    org_id="00000000-0000-0000-0000-000000000001",
    full_name=(
        "ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ АВТОНОМНОЕ ОБРАЗОВАТЕЛЬНОЕ УЧРЕЖДЕНИЕ ВЫСШЕГО ОБРАЗОВАНИЯ "
        '"БАЛТИЙСКИЙ ФЕДЕРАЛЬНЫЙ УНИВЕРСИТЕТ ИМЕНИ ИММАНУИЛА КАНТА"'
    ),
    short_name='ФГАОУ ВО "БФУ ИМ. И. КАНТА"',
    is_branch=False,
    head_org_id=None,
    post_address="236041, обл. Калининградская, г. Калининград, ул. А.Невского, д. 14",
    ogrn="0",
    inn="0",
    levels=("Высшее образование - бакалавриат",),
)
COLLEGE = replace(
    UNIVERSITY,
    org_id="00000000-0000-0000-0000-000000000002",
    full_name=(
        "государственное автономное учреждение Калининградской области профессиональная "
        "образовательная организация «Колледж сервиса и туризма»"
    ),
    short_name="ГАУ КО ПОО КСТ",
    levels=("Среднее профессиональное образование",),
)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({}, None),
        (
            {
                "full_name": "Муниципальное автономное общеобразовательное учреждение СОШ № 7",
                "levels": (),
            },
            "NOT_COLLEGE_OR_UNIVERSITY",
        ),
        (
            {
                "full_name": "ФЕДЕРАЛЬНОЕ ГОСУДАРСТВЕННОЕ КАЗЕННОЕ ВОЕННОЕ ОБРАЗОВАТЕЛЬНОЕ УЧРЕЖДЕНИЕ ВЫСШЕГО ОБРАЗОВАНИЯ"
            },
            "DEPARTMENTAL_INSTITUTION",
        ),
        (
            {
                "full_name": "Автономная некоммерческая организация профессионального образования «Бизнес колледж»"
            },
            "NOT_PUBLIC",
        ),
        (
            {
                "full_name": "федеральное государственное бюджетное профессиональное образовательное специальное учебно-воспитательное учреждение"
            },
            "SPECIAL_CLOSED_INSTITUTION",
        ),
    ],
)
def test_classify(changes: dict[str, object], reason: str | None) -> None:
    assert ob.classify(replace(UNIVERSITY, **changes)) == reason  # type: ignore[arg-type]


def test_ownership_from_legal_form() -> None:
    assert ob.ownership(UNIVERSITY.full_name) == "federal"
    assert ob.ownership(COLLEGE.full_name) == "regional"
    assert ob.ownership("Муниципальное бюджетное учреждение") == "municipal"
    assert ob.ownership("Автономная некоммерческая организация") == "private"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Инженерно-технический институт БФУ им. И. Канта", "1"),  # abbreviation
        ("Балтийский федеральный университет имени И.Канта", "1"),  # two stems, no extra word
        ("Колледж сервиса и туризма", "2"),
        ("Российский государственный университет туризма и сервиса", None),  # type conflict
        ("Балтийский институт психологии", None),  # one shared stem only
        ("Колледж управления, права и информационных технологий МЭСИ", None),
        (None, None),
    ],
)
def test_link(name: str | None, expected: str | None) -> None:
    keys = [ob.name_key(UNIVERSITY), ob.name_key(COLLEGE)]
    found = ob.link(name, keys)
    assert (found.org_id[-1] if found.org_id else None) == expected


def test_read_orgs_takes_active_records_of_region_and_local_branches(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?><OpenData><Certificates>
    <Certificate><StatusName>Действующее</StatusName><RegionName>Калининградская область</RegionName>
      <Supplements><Supplement><StatusName>Действующее</StatusName>
        <ActualEducationOrganization><Id>a</Id><FullName>Колледж А</FullName><ShortName>КА</ShortName>
          <IsBranch></IsBranch><PostAddress>г. Калининград, ул. Тестовая, д. 1</PostAddress>
          <HeadName>Иванов Иван</HeadName></ActualEducationOrganization>
        <EducationalPrograms><EducationalProgram><EduLevelName>Среднее профессиональное образование</EduLevelName></EducationalProgram></EducationalPrograms>
      </Supplement></Supplements></Certificate>
    <Certificate><StatusName>Действующее</StatusName><RegionName>г. Москва</RegionName>
      <Supplements><Supplement><StatusName>Действующее</StatusName>
        <ActualEducationOrganization><Id>b</Id><FullName>Филиал Б</FullName><ShortName>ФБ</ShortName>
          <IsBranch>1</IsBranch><PostAddress>г. Калининград, ул. Иная, д. 2</PostAddress></ActualEducationOrganization>
      </Supplement><Supplement><StatusName>Действующее</StatusName>
        <ActualEducationOrganization><Id>c</Id><FullName>Головной В</FullName><ShortName>ГВ</ShortName>
          <PostAddress>г. Москва, ул. Любая, д. 3</PostAddress></ActualEducationOrganization>
      </Supplement></Supplements></Certificate>
    <Certificate><StatusName>Недействующее</StatusName><RegionName>Калининградская область</RegionName>
      <Supplements><Supplement><StatusName>Действующее</StatusName>
        <ActualEducationOrganization><Id>d</Id><FullName>Старый Г</FullName></ActualEducationOrganization>
      </Supplement></Supplements></Certificate>
    </Certificates></OpenData>"""
    raw = tmp_path / "registry.zip"
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("data.xml", xml.encode("utf-8"))
    orgs = ob.read_orgs(raw, "Калининградская область", "Калининград")
    assert [(o.org_id, o.is_branch) for o in orgs] == [("a", False), ("b", True)]
    assert orgs[0].levels == ("Среднее профессиональное образование",)
    assert "Иванов" not in repr(orgs)


@pytest.mark.parametrize(
    ("full_name", "levels", "reason"),
    [
        ("Муниципальное бюджетное общеобразовательное учреждение СОШ № 7", (), None),
        (
            "Государственное казенное общеобразовательное учреждение кадетская школа",
            (),
            "DEPARTMENTAL_INSTITUTION",
        ),
        ("Частное общеобразовательное учреждение «Школа»", (), "NOT_PUBLIC"),
        (UNIVERSITY.full_name, ("Высшее образование - бакалавриат",), "NOT_A_SCHOOL"),
        ("Муниципальное автономное дошкольное образовательное учреждение", (), "NOT_A_SCHOOL"),
    ],
)
def test_school_reason(full_name: str, levels: tuple[str, ...], reason: str | None) -> None:
    org = replace(UNIVERSITY, full_name=full_name, levels=levels)
    assert ob.school_reason(org) == reason
