"""Kaliningrad city administration (klgd.ru): municipal schools and kindergartens.

The city is the founder of these institutions, so its published roster confirms
municipal ownership (PRODUCT_SPEC §2: public ownership confirmed by a registry).
The list pages give the roster; the institutions map gives building coordinates,
keyed by the same ``detail.php?ID=`` as the lists.

Only facts needed for analysis are parsed: name, district, address, licence and
accreditation references, building coordinates. Directors, phones and e-mails
stay in the raw archive and are never copied into processed data or bundles.
"""

from __future__ import annotations

import hashlib
import html
import io
import json
import re
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from .fetch import USER_AGENT
from .manifest import DatasetManifest, load_registry, write_dataset_manifest
from .paths import raw_dir
from .store import freeze_raw

SOURCE_ID = "klgd_education"
BASE = "https://www.klgd.ru/activity/education/institutions/"
LISTS = {"school": "schools/index.php", "kindergarten": "garden/index.php"}
MAP_PAGE = "map/index.php"
MAP_CATEGORIES = {"s29586": "school", "s29585": "kindergarten"}
MUNICIPAL_FORMS = re.compile(r"^(М[АБК]ОУ|М[АБК]ДОУ)\b")
ENCODING = "cp1251"
ZIP_DATE = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Institution:
    institution_id: str
    kind: str
    name: str
    district: str | None
    address: str
    detail_url: str


@dataclass(frozen=True)
class Building:
    """One map point. ``institution_id`` is the listed institution it belongs to."""

    map_id: str
    institution_id: str | None
    kind: str
    name: str
    lat: float
    lon: float
    address_short: str | None
    licence: str | None
    accreditation: str | None


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_list(page: str, kind: str) -> list[Institution]:
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)
        if len(cells) < 2:
            continue
        link = re.search(r'href="([^"]*detail\.php\?ID=(\d+))"[^>]*>(.*?)</a>', cells[0], re.S)
        if not link:
            continue
        district, _, address = cells[1].partition("<br>")
        rows.append(
            Institution(
                institution_id=link.group(2),
                kind=kind,
                name=_text(link.group(3)),
                district=_text(district) or None,
                address=_text(address),
                detail_url=BASE + LISTS[kind].rsplit("/", 1)[0] + "/detail.php?ID=" + link.group(2),
            )
        )
    return rows


def parse_map(page: str) -> list[Building]:
    decoder = json.JSONDecoder()
    buildings = []
    for match in re.finditer(r'\{"[^{}]*?"LAT":\s*-?\d', page):
        try:
            item, _ = decoder.raw_decode(page[match.start() :])
        except json.JSONDecodeError:
            continue
        kind = MAP_CATEGORIES.get(str(item.get("cat")))
        found = re.search(r"ID=(\d+)", str(item.get("url", "")))
        if kind is None or not found:
            continue
        buildings.append(
            Building(
                map_id=found.group(1),
                institution_id=None,
                kind=kind,
                name=str(item["name"]).strip(),
                lat=float(item["LAT"]),
                lon=float(item["LNG"]),
                address_short=(str(item.get("adress_short") or "").strip() or None),
                licence=(_text(str(item.get("license") or "")) or None),
                accreditation=(_text(str(item.get("akkredit") or "")) or None),
            )
        )
    return buildings


def is_municipal(name: str) -> bool:
    return bool(MUNICIPAL_FORMS.match(name))


def name_key(name: str) -> str:
    """Name comparison key: case, spaces, quotes, dots, dashes and № are ignored."""

    return re.sub(r"[\s«»\"'.\-–—]+", "", name.lower()).replace("№", "")


def fetch(
    retrieved: date | None = None, pause_s: float = 0.5, max_pages: int = 40
) -> DatasetManifest:
    """Download every list page and the map once into a deterministic raw zip."""

    stamp = (retrieved or datetime.now(UTC).date()).strftime("%Y%m%d")
    dataset_id = f"{SOURCE_ID}.{stamp}"
    target = raw_dir() / SOURCE_ID / f"klgd-education-{stamp}.zip"
    entry = load_registry().get(SOURCE_ID)
    if not target.exists():
        pages: dict[str, bytes] = {}
        with httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True
        ) as client:
            for kind, path in LISTS.items():
                seen: set[str] = set()
                for number in range(1, max_pages + 1):
                    url = BASE + path + ("" if number == 1 else f"?PAGEN_1={number}")
                    response = client.get(url)
                    response.raise_for_status()
                    ids = {
                        row.institution_id
                        for row in parse_list(response.content.decode(ENCODING), kind)
                    }
                    if not ids - seen:
                        break
                    seen |= ids
                    pages[f"{kind}-p{number:02d}.html"] = response.content
                    time.sleep(pause_s)
            response = client.get(BASE + MAP_PAGE)
            response.raise_for_status()
            pages["map.html"] = response.content
        target.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(pages):
                info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, pages[name])
        target.write_bytes(buffer.getvalue())
    checksum = freeze_raw(target)
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        source_id=SOURCE_ID,
        source_url=BASE,  # type: ignore[arg-type]
        retrieved_at=datetime.fromtimestamp(target.stat().st_mtime, UTC),
        publication_date=None,
        valid_from=None,
        valid_to=None,
        geography="Городской округ «Город Калининград»",
        license=entry.license,
        access_method=entry.access_method,
        raw_path=str(target.relative_to(raw_dir())),
        checksum=checksum,
        schema_mapping={
            "list rows": "name, district, address, detail.php?ID",
            "map items": "ID, cat, LAT, LNG, adress_short, license, akkredit",
        },
        coverage="Муниципальные общеобразовательные и дошкольные учреждения, опубликованные учредителем",
        limitations=[
            "Только муниципальные учреждения: региональные, федеральные и частные не входят",
            "Координаты корпусов — с карты сайта администрации, точность не заявлена",
        ],
        status="fetched",
    )
    write_dataset_manifest(manifest)
    return manifest


@dataclass(frozen=True)
class Roster:
    institutions: dict[str, Institution]
    buildings: list[Building]
    qa: dict[str, Any]

    def buildings_of(self, institution_id: str) -> list[Building]:
        return [b for b in self.buildings if b.institution_id == institution_id]

    def record_hash(self, institution_id: str) -> str:
        record = {
            "institution": asdict(self.institutions[institution_id]),
            "buildings": [asdict(b) for b in self.buildings_of(institution_id)],
        }
        payload = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_roster(raw_zip: Path) -> Roster:
    """Roster from the founder's lists; map points attach by their own ID or by name.

    A map point whose ID is not listed belongs to a listed institution only when
    the names match exactly (another building of the same institution). Points
    of institutions absent from the lists are not used and are reported in QA.
    """

    institutions: dict[str, Institution] = {}
    points: list[Building] = []
    with zipfile.ZipFile(raw_zip) as archive:
        for name in sorted(archive.namelist()):
            page = archive.read(name).decode(ENCODING, errors="strict")
            if name == "map.html":
                points = parse_map(page)
                continue
            kind = name.split("-p", 1)[0]
            for row in parse_list(page, kind):
                institutions.setdefault(row.institution_id, row)
    by_name: dict[tuple[str, str], list[str]] = {}
    for institution in institutions.values():
        by_name.setdefault((institution.kind, name_key(institution.name)), []).append(
            institution.institution_id
        )
    attached: list[Building] = []
    unlisted: list[dict[str, str]] = []
    for point in points:
        owner: str | None = None
        if point.map_id in institutions and institutions[point.map_id].kind == point.kind:
            owner = point.map_id
        else:
            candidates = by_name.get((point.kind, name_key(point.name)), [])
            owner = candidates[0] if len(candidates) == 1 else None
        if owner is None:
            unlisted.append({"map_id": point.map_id, "kind": point.kind, "name": point.name})
            continue
        attached.append(replace(point, institution_id=owner))
    unique: dict[tuple[str, float, float], Building] = {}
    for building in attached:
        key = (str(building.institution_id), round(building.lat, 5), round(building.lon, 5))
        unique.setdefault(key, building)
    kept = sorted(unique.values(), key=lambda b: (b.kind, int(b.map_id)))
    covered = {b.institution_id for b in kept}
    qa = {
        "institutions": {
            kind: sum(1 for i in institutions.values() if i.kind == kind) for kind in LISTS
        },
        "buildings": {kind: sum(1 for b in kept if b.kind == kind) for kind in LISTS},
        "institutions_without_coordinates": sorted(i for i in institutions if i not in covered),
        "map_points_not_in_founder_lists": unlisted,
        "duplicate_map_points": len(attached) - len(unique),
        "non_municipal_names": sorted(
            i.name for i in institutions.values() if not is_municipal(i.name)
        ),
    }
    return Roster(institutions=institutions, buildings=kept, qa=qa)
