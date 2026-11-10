"""Rosobrnadzor accreditation registry (open data of ИС ГА): colleges and universities.

Rule set ``obrnadzor-college-university-rules-v1`` (PRODUCT_SPEC §2):

- only active certificates and active supplements;
- an organisation or branch counts when it has accredited higher education or
  its full name is that of a professional or higher education organisation;
- federal «казённые» institutions (military, security, penitentiary), special
  closed institutions and social institutions do not serve city residents;
- ownership comes from the legal form in the official full name: federal,
  regional (state institution of the region), municipal; non-profit and
  private organisations are not public.

Names link OSM buildings to an organisation (``edu-name-link-v1``): the
organisation's abbreviation as a word; or its distinctive name words compared
by six-letter stem — at least two shared stems and no other distinctive word
in the OSM name (three shared stems allow two other words), one shared stem
only when it is the organisation's only stem and the OSM name has no other;
the type word (колледж, университет ...) must not conflict.
Directors, phones and e-mails are never read into processed data.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SOURCE_ID = "obrnadzor_accreditation"
RULES_VERSION = "obrnadzor-college-university-rules-v1"
LINK_RULES_VERSION = "edu-name-link-v1"
REGISTRY_PAGE = "https://isga.obrnadzor.gov.ru/accredreestr/"
LEGAL_ABBREVIATIONS = {
    "ФГАОУ",
    "ФГБОУ",
    "ФГКОУ",
    "ФГОУ",
    "ГАУ",
    "ГБУ",
    "ГАОУ",
    "ГБОУ",
    "ГБПОУ",
    "ГАПОУ",
    "КО",
    "ПОО",
    "ВО",
    "ПО",
    "СПО",
    "АНО",
    "АНООВО",
    "АНПОО",
    "ЧОУ",
    "ИМ",
    "РФ",
    "ФКП",
    "ФКОУ",
    "МАОУ",
    "МБОУ",
}
GENERIC_WORDS = {
    "колледж",
    "техникум",
    "университет",
    "институт",
    "академия",
    "училище",
    "лицей",
    "высшего",
    "высшее",
    "образования",
    "образование",
    "профессиональный",
    "профессиональная",
    "профессиональное",
    "государственный",
    "государственная",
    "государственное",
    "федеральный",
    "федеральная",
    "федеральное",
    "калининградский",
    "калининградская",
    "калининградское",
    "калининградской",
    "филиал",
    "им",
    "имени",
    "российский",
    "российская",
    "образовательное",
    "образовательная",
    "учреждение",
    "бюджетное",
    "автономное",
    "области",
    "организация",
    "корпус",
    "представительство",
    "национальный",
    "исследовательский",
}
TYPE_WORDS = ("колледж", "техникум", "университет", "институт", "академи", "училищ", "лицей")
GENERIC_STEMS = {word[:6] for word in GENERIC_WORDS}


@dataclass(frozen=True)
class EduOrg:
    org_id: str
    full_name: str
    short_name: str
    is_branch: bool
    head_org_id: str | None
    post_address: str
    ogrn: str
    inn: str
    levels: tuple[str, ...]


def _text(element: Any, tag: str) -> str:
    found = element.find(tag)
    return re.sub(r"\s+", " ", found.text).strip() if found is not None and found.text else ""


def read_orgs(raw_zip: Path, region_name: str, address_hint: str) -> list[EduOrg]:
    """Active organisations and branches of a region, plus branches located there."""

    from lxml import etree  # type: ignore[import-untyped]

    found: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(raw_zip) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".xml"))
        with archive.open(name) as stream:
            for _, certificate in etree.iterparse(
                stream, events=("end",), tag="Certificate", huge_tree=True
            ):
                if _text(certificate, "StatusName") == "Действующее":
                    in_region = _text(certificate, "RegionName") == region_name
                    for supplement in certificate.iterfind("Supplements/Supplement"):
                        if _text(supplement, "StatusName") != "Действующее":
                            continue
                        org = supplement.find("ActualEducationOrganization")
                        if org is None:
                            continue
                        address = _text(org, "PostAddress")
                        if not in_region and address_hint not in address:
                            continue
                        levels = {
                            _text(p, "EduLevelName")
                            for p in supplement.iterfind("EducationalPrograms/EducationalProgram")
                        }
                        record = found.setdefault(
                            _text(org, "Id"),
                            {
                                "org_id": _text(org, "Id"),
                                "full_name": _text(org, "FullName"),
                                "short_name": _text(org, "ShortName"),
                                "is_branch": _text(org, "IsBranch") == "1",
                                "head_org_id": _text(org, "HeadEduOrgId") or None,
                                "post_address": address,
                                "ogrn": _text(org, "OGRN"),
                                "inn": _text(org, "INN"),
                                "levels": set(),
                            },
                        )
                        record["levels"] |= {level for level in levels if level}
                certificate.clear()
                while certificate.getprevious() is not None:
                    del certificate.getparent()[0]
    return [
        EduOrg(**{**record, "levels": tuple(sorted(record["levels"]))})
        for _, record in sorted(found.items())
    ]


def ownership(full_name: str) -> str:
    name = full_name.lower()
    # Check negated legal forms before the positive state/federal/municipal
    # stems: «негосударственное» contains «государственн». Legal abbreviations
    # are whole prefix tokens, not ordinary words or quoted institution names.
    if (
        re.search(r"\bне[\s‐‑–—-]*государственн", name)
        or re.search(r"^\W*(?:ноу|чоу|чпоу|нчоу|ано|анпо|анпоо|аноо|аноово)\b", name)
        or "некоммерческ" in name
        or "частн" in name
        or "с ограниченной ответственностью" in name
    ):
        return "private"
    if "муниципальн" in name:
        return "municipal"
    if "федеральн" in name:
        return "federal"
    if "государственн" in name:
        return "regional"
    return "unknown"


def classify(org: EduOrg) -> str | None:
    """None when the organisation is a public college or university, else the reason."""

    name = org.full_name.lower()
    higher = any(level.startswith(("Высшее", "ВО ")) for level in org.levels)
    professional = any(word in name for word in ("профессиональн", "высшего образования"))
    if not (higher or professional):
        return "NOT_COLLEGE_OR_UNIVERSITY"
    if "казенн" in name or "казённ" in name:
        return "DEPARTMENTAL_INSTITUTION"
    if "специальное учебно-воспитательное" in name:
        return "SPECIAL_CLOSED_INSTITUTION"
    if "социальное учреждение" in name:
        return "SOCIAL_INSTITUTION"
    if ownership(org.full_name) not in {"federal", "regional", "municipal"}:
        return "NOT_PUBLIC"
    return None


GENERAL_LEVELS = {
    "Начальное общее образование",
    "Основное общее образование",
    "Среднее общее образование",
}


def school_reason(org: EduOrg) -> str | None:
    """None when the organisation is a public general-education school, else the reason."""

    name = org.full_name.lower()
    if "общеобразовательн" not in name and not GENERAL_LEVELS & set(org.levels):
        return "NOT_A_SCHOOL"
    if any(word in name for word in ("профессиональн", "высшего образования")):
        return "NOT_A_SCHOOL"
    if "казенн" in name or "казённ" in name:
        return "DEPARTMENTAL_INSTITUTION"
    if ownership(org.full_name) not in {"federal", "regional", "municipal"}:
        return "NOT_PUBLIC"
    return None


def record_hash(org: EduOrg) -> str:
    payload = json.dumps(asdict(org), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _quoted(text: str) -> str:
    found = re.search(r"[«\"“](.+?)[»\"”]", text)
    return found.group(1) if found else text


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[а-яё]+", text.lower().replace("ё", "е")) if len(w) > 1]


def _stem(word: str) -> str:
    return word[:6]


def _types(text: str) -> set[str]:
    lowered = text.lower()
    return {t for t in TYPE_WORDS if t in lowered}


@dataclass(frozen=True)
class NameKey:
    org_id: str
    abbreviation: str | None
    stems: frozenset[str]
    types: frozenset[str]


def _distinctive(text: str) -> set[str]:
    return {_stem(w) for w in _words(text) if _stem(w) not in GENERIC_STEMS}


def name_key(org: EduOrg) -> NameKey:
    core = _quoted(org.full_name)
    stems = frozenset(_distinctive(core))
    first = re.findall(r"[А-ЯЁA-Z]{2,6}", _quoted(org.short_name))
    abbreviation = next((t for t in first if t not in LEGAL_ABBREVIATIONS), None)
    return NameKey(org.org_id, abbreviation, stems, frozenset(_types(core)))


def link_score(key: NameKey, name: str) -> int:
    """3 for an abbreviation match, the number of matched stems, or 0."""

    if key.abbreviation and re.search(rf"(?<![А-ЯЁA-Z]){key.abbreviation}(?![А-ЯЁA-Z])", name):
        return 3
    types = _types(name)
    if key.types and types and not key.types & types:
        return 0
    stems = _distinctive(name)
    matched = len(key.stems & stems)
    extra = len(stems - key.stems)
    if len(key.stems) >= 2 and ((matched >= 2 and extra == 0) or (matched >= 3 and extra <= 2)):
        return matched
    if len(key.stems) == 1 and matched == 1 and extra == 0:
        return 1
    return 0


@dataclass
class Linked:
    org_id: str | None
    score: int
    ambiguous: list[str] = field(default_factory=list)


def link(name: str | None, keys: list[NameKey]) -> Linked:
    if not name:
        return Linked(None, 0)
    scored = sorted(((link_score(k, name), k.org_id) for k in keys), reverse=True)
    best = [org for score, org in scored if score and score == scored[0][0]]
    if not best:
        return Linked(None, 0)
    if len(best) > 1:
        return Linked(None, scored[0][0], best)
    return Linked(best[0], scored[0][0])
