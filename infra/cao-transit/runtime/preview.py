"""Serve the immutable ЦАО walk export through the deployed atlas contract."""

import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from a2_api.api.application import create_app
from transit_preview import TRANSIT, transit_inventory
from a2_api.assistant.consultant import Consultant
from a2_api.assistant.consultant.model import consultant_enabled
from a2_api.core.config import RuntimeSettings
from a2_api.core.errors import DomainError
from a2_api.core.registry import ProductRegistry
from a2_api.services.analyses import AnalysisService
from a2_contracts.common import canonical_hash
from a2_contracts.v2.api import AreaOption, JobAccepted, JobView, SnapshotAvailability, TerritoryOption, UrbanPassengerScope
from a2_contracts.v2.requests import UrbanPassengerRequest
from a2_contracts.v2.results import AnalysisResult
from fastapi.responses import RedirectResponse

DATA = Path("/preview/data-500m.json")
META = Path("/preview/metadata.json")
AREA = "moscow-relation-2162196"
ANALYSIS = "cao-walk-500m"
CATEGORIES = {
    "adult_polyclinic": "Взрослые поликлиники",
    "child_polyclinic": "Детские поликлиники",
    "kindergarten": "Детские сады",
    "school": "Школы",
    "college_university": "Колледжи и вузы",
    "rail_terminal": "Железнодорожные вокзалы",
    "bus_terminal": "Автовокзалы",
    "shopping_center": "Торговые центры",
}
HALF_LIFE_SECONDS = 900
MOSCOW = ZoneInfo("Europe/Moscow")
PRIORITIES = {"low": 1, "medium": 2, "high": 3}
PRIORITY_NAMES = {weight: name for name, weight in PRIORITIES.items()}
BASE_CATEGORIES = ("adult_polyclinic", "school", "shopping_center")
VARIANT = re.compile(r"(cao-(?:walk|transit)-500m)-(\d{10})-([0-3]{8})")
SCOPE_TEXT = "ЦАО · сетка 500 м. Поиск объектов в радиусе 2 км, до 4 км для пустых категорий."


def decode_line(encoded):
    coordinates = []
    values = [0, 0]
    delta = shift = axis = 0
    for char in encoded:
        value = ord(char) - 63
        delta |= (value & 31) << shift
        if value >= 32:
            shift += 5
            continue
        values[axis] += ~(delta >> 1) if delta & 1 else delta >> 1
        if axis == 1:
            coordinates.append([values[1] / 1e6, values[0] / 1e6])
        delta = shift = 0
        axis = 1 - axis
    if shift or axis:
        raise ValueError("Incomplete encoded route")
    return coordinates


def load_result(meta):
    raw = DATA.read_bytes()
    digest = sha256(raw).hexdigest()
    if digest != meta["sha256"]:
        raise ValueError("ЦАО export checksum changed")
    data = json.loads(raw)
    req = {
        **meta["request"],
        "area_id": AREA,
        "categories": list(BASE_CATEGORIES),
        "priorities": dict.fromkeys(BASE_CATEGORIES, "medium"),
    }
    version = {
        **meta["versions"],
        "code_version": "cao-walk-500m-" + digest,
    }
    start = datetime.fromisoformat(req["departure_at"])
    source = "cao-walk-500m-" + digest
    refs = [source]
    metrics = []
    scores = []
    journeys = []
    destinations = {}
    destination_categories = defaultdict(set)
    features = []

    def feature(kind, key, geometry, **properties):
        return dict(
            entity_kind=kind, entity_id=key, territory_id=AREA, geometry=geometry,
            geometry_kind="actual", source_refs=refs, **properties,
        )

    def score(key, name, value, subject, components):
        return dict(
            score_id=key, name=name, value=value, status="available",
            completeness="partial", methodology_version=version["methodology_version"],
            reason_code="OBSERVED_ROUTE_ESTIMATE", components=components,
            evidence_refs=refs, subject=subject, ranking_eligible=False,
        )

    for cell in data["features"]:
        point = cell["properties"]
        point_id = point["id"]
        cell_id = point_id + "-cell"
        name = point["street"] or ("Пешеходная дорожка" if point["walk_verified"]
                                  else "Ячейка без подтверждённого старта")
        features.append(feature("cell", cell_id, cell["geometry"], point_id=point_id, name=name))
        lat, lon = point["origin"]
        features.append(feature("sample_point", point_id,
                                dict(type="Point", coordinates=[lon, lat]),
                                cell_id=cell_id, name=name))
        point_subject = dict(kind="sample_point", point_id=point_id, territory_id=AREA, mode="walk")
        for category, label in CATEGORIES.items():
            match = point["matches"].get(category)
            metric_id = f"{point_id}-{category}-time"
            subject = dict(point_subject, category=category)
            seconds = match["travel_seconds"] if match else None
            journey_id = None
            if match:
                destination = match["destination_id"]
                destination_categories[destination].add(category)
                if destination not in destinations:
                    lat, lon = match["target"]
                    destinations[destination] = feature(
                        "destination", destination, dict(type="Point", coordinates=[lon, lat]),
                        name=match["name"],
                    )
                subject["destination_id"] = destination
                journey_id = f"{point_id}-{category}-walk"
                arrival = start + timedelta(seconds=seconds)
                coordinates = decode_line(match["shape"][0])
                if len(coordinates) < 2:
                    raise ValueError("Short exported route line")
                journeys.append(dict(
                    journey_id=journey_id, scope="urban", origin_node_id=point_id,
                    destination_node_id=destination, point_id=point_id,
                    destination_object_id=destination, requested_departure_at=start,
                    departure_at=start, arrival_at=arrival, total_seconds=seconds,
                    initial_wait_seconds=0.0, total_wait_seconds=0.0, path_seconds=seconds,
                    legs=[dict(
                        mode="walk", kind="walk", from_node_id=point_id, to_node_id=destination,
                        start_at=start, end_at=arrival, duration_seconds=seconds, service_id=None,
                        evidence_refs=refs,
                        geometry=dict(type="LineString", coordinates=coordinates, evidence_refs=refs),
                    )],
                    geometry=None, geometry_kind="none", status="available",
                    reason_code="MINIMUM_NOT_PROVEN", snapshot_id=req["snapshot_id"],
                    completeness="partial", traffic_mode="static", time_kind="movement",
                    reference_id=None, evidence_refs=refs,
                ))
                estimate = 100 * 2 ** (-seconds / HALF_LIFE_SECONDS)
                category_score = score(
                    f"{point_id}-{category}-estimate", "observed_route_accessibility",
                    estimate, subject, [dict(kind="metric", id=metric_id)],
                )
                scores.append(category_score)
            metrics.append(dict(
                metric_id=metric_id, name=f"Время пешком · {label.lower()}", value=seconds,
                unit="seconds", status="available" if match else "unknown",
                reason_code="MINIMUM_NOT_PROVEN" if match else "OBSERVED_ROUTE_UNAVAILABLE",
                source_refs=refs, subject=subject, completeness="partial", time_kind="movement",
                journey_id=journey_id,
            ))

    for destination, record in destinations.items():
        features.append(dict(record, categories=sorted(destination_categories[destination])))
    features.append(feature("territory", AREA, data["boundary"], name="Центральный административный округ"))
    limitations = [
        "OBSERVED_ROUTE_ESTIMATE", "MINIMUM_NOT_PROVEN", "NOT_A_CITY_RANKING",
        SCOPE_TEXT,
        "Цвет ячеек отражает взвешенную оценку по выбранным категориям. Параметр демо H — 15 минут.",
        "Линия показывает рассчитанный участок пути по сети Valhalla.",
    ]
    return dict(
        schema_version="2", analysis_id=ANALYSIS, request=req, analysis_kind="urban_passenger",
        snapshot_id=req["snapshot_id"], methodology_version=version["methodology_version"],
        config_hash=version["config_hash"], traffic_mode="static", status="partial",
        metrics=metrics, scores=scores, journeys=journeys, references=[], counts=[], last_returns=[],
        evidence_refs=[dict(
            evidence_id=source, source_id="cao-walk-500m",
            url="https://waydex.mhimf.net/cao-demo/data-500m.json",
            retrieved_at=meta["published_at"], observed_at=None, section="features",
        )],
        limitations=limitations, versions=version, result_hash="pending",
        map_data=dict(status="available", completeness="partial", reason_code=None, features=features),
    )


def variant_result(inventory, analysis_id, requested_at, priorities):
    metrics = [item for item in inventory["metrics"] if item["subject"]["category"] in priorities]
    scores = [item for item in inventory["scores"] if item["subject"]["category"] in priorities]
    by_point = defaultdict(list)
    for item in scores:
        by_point[item["subject"]["point_id"]].append(item)
    for point_id, categories in by_point.items():
        if len(categories) != len(priorities):
            continue
        total_weight = sum(PRIORITIES[priority] for priority in priorities.values())
        value = sum(item["value"] * PRIORITIES[priorities[item["subject"]["category"]]]
                    for item in categories) / total_weight
        first = categories[0]
        scores.append(dict(first, score_id=point_id + "-estimate",
                           name="Предварительная взвешенная оценка", value=value,
                           subject=dict(kind="sample_point", territory_id=AREA, point_id=point_id, mode=inventory["request"]["passenger_mode"]),
                           components=[dict(kind="score", id=item["score_id"]) for item in categories]))
    route_ids = {item["journey_id"] for item in metrics if item["journey_id"]}
    journeys = []
    for item in inventory["journeys"]:
        if item["journey_id"] not in route_ids:
            continue
        shift = requested_at - item["requested_departure_at"]
        arrival = item["arrival_at"] + shift
        legs = [dict(leg, start_at=leg["start_at"]+shift, end_at=leg["end_at"]+shift) for leg in item["legs"]]
        journeys.append(dict(item, requested_departure_at=requested_at, departure_at=requested_at,
                             arrival_at=arrival, legs=legs))
    request = dict(inventory["request"], departure_at=requested_at, priorities=priorities,
                   categories=list(priorities))
    result = AnalysisResult.model_validate(dict(inventory, analysis_id=analysis_id, request=request,
                                                 metrics=metrics, scores=scores, journeys=journeys))
    result.result_hash = canonical_hash(result.model_dump(mode="json", exclude={"result_hash"}))
    return result


class PreviewService(AnalysisService):
    def __init__(self, registry, inventory, snapshot, transit, repository=None):
        super().__init__(registry, repository)
        self.inventory = inventory
        self.snapshot = snapshot
        self.valid_from = datetime.combine(snapshot.valid_from, time.min, MOSCOW)
        self.valid_to = datetime.combine(snapshot.valid_to + timedelta(days=1), time.min, MOSCOW)
        self.initial = variant_result(inventory, ANALYSIS,
                                      datetime.fromisoformat(inventory["request"]["departure_at"]),
                                      dict.fromkeys(BASE_CATEGORIES, "medium"))
        self.cache = {ANALYSIS: self.initial}
        self.transit = transit
        if transit is not None:
            self.transit_start = transit["request"]["departure_at"]
            self.cache[TRANSIT] = variant_result(transit, TRANSIT, self.transit_start,
                                                 dict.fromkeys(BASE_CATEGORIES, "medium"))

    def capabilities(self):
        value = super().capabilities()
        value.snapshots = [self.snapshot]
        value.categories = list(CATEGORIES)
        value.urban_passenger_scopes = [UrbanPassengerScope(
            scope_id=ANALYSIS, snapshot_id=self.snapshot.snapshot_id, city_id="moscow",
            passenger_modes=["walk"], categories=list(CATEGORIES), traffic_modes=["static"],
            valid_from=self.valid_from, valid_to=self.valid_to, whole_city=False, area_ids=[AREA],
        )]
        if self.transit:
            value.urban_passenger_scopes.append(UrbanPassengerScope(
                scope_id=TRANSIT, snapshot_id=self.snapshot.snapshot_id, city_id="moscow",
                passenger_modes=["public_transport"], categories=list(CATEGORIES), traffic_modes=["static"],
                valid_from=self.transit_start, valid_to=self.transit_start+timedelta(minutes=15),
                whole_city=False, area_ids=[AREA],
            ))
        for item in value.analyses:
            if item.analysis_kind == "urban_passenger":
                item.calculation_available = True
                item.reason_code = None
        return value

    async def analyse(self, request, key, request_id, scope, **kwargs):
        if (not isinstance(request, UrbanPassengerRequest) or request.city_id != "moscow"
                or request.area_id != AREA or request.passenger_mode not in {"walk", "public_transport"}
                or request.traffic_mode != "static" or request.snapshot_id != self.snapshot.snapshot_id
                or not set(request.categories) <= CATEGORIES.keys()
                or not self.valid_from <= request.departure_at < self.valid_to):
            raise DomainError(422, "invalid_request", "Доступен пеший расчёт ЦАО по готовой сетке.")
        weights = "".join(str(PRIORITIES.get(request.priorities.get(category), 0)) for category in CATEGORIES)
        stem = ANALYSIS
        if request.passenger_mode == "public_transport":
            if not self.transit or request.departure_at != self.transit_start:
                raise DomainError(422, "invalid_request", "ОТ по ЦАО рассчитан на 13 ноября, 08:45.")
            stem = TRANSIT
        identifier = f"{stem}-{int(request.departure_at.timestamp())}-{weights}"
        return JobAccepted(job_id=identifier, status="succeeded", request_id=request_id)

    async def job(self, job_id, scope, *, include_progress=False):
        await self.analysis(job_id, scope)
        now = datetime.now(timezone.utc)
        return JobView(job_id=job_id, status="succeeded", request_id=job_id, created_at=now,
                       started_at=now, finished_at=now, analysis_id=job_id, scenario_run_id=None, error=None)

    async def analysis(self, analysis_id, scope):
        if analysis_id in self.cache:
            return self.cache[analysis_id]
        match = VARIANT.fullmatch(analysis_id)
        if match:
            stem, stamp, weights = match.groups()
            date = datetime.fromtimestamp(int(stamp), MOSCOW)
            if self.valid_from <= date < self.valid_to and not date.minute % 15 and not date.second and weights != "00000000":
                priorities = {category: PRIORITY_NAMES[int(weight)]
                              for category, weight in zip(CATEGORIES, weights, strict=True) if weight != "0"}
                inventory = self.inventory
                if stem == TRANSIT:
                    if not self.transit or date != self.transit_start:
                        raise DomainError(404, "unknown_id", "Analysis not found")
                    inventory = self.transit
                result = variant_result(inventory, analysis_id, date, priorities)
                if len(self.cache) >= 5:
                    self.cache.pop(next(key for key in self.cache if key not in {ANALYSIS, TRANSIT}))
                self.cache[analysis_id] = result
                return result
        raise DomainError(404, "unknown_id", "Analysis not found")

    async def accepted_snapshot(self, result, scope):
        return None

    async def proposals(self, analysis_id, scope):
        await self.analysis(analysis_id, scope)
        return []


def build_app():
    meta = json.loads(META.read_text())
    inventory = load_result(meta)
    transit = transit_inventory(inventory, CATEGORIES, decode_line, MOSCOW)
    snapshot = SnapshotAvailability.model_validate(meta["snapshot"])
    registry = ProductRegistry(
        territories=[TerritoryOption(territory_id="moscow", name="Москва",
                                     level="city", timezone="Europe/Moscow")],
        areas=[AreaOption(area_id=AREA, city_id="moscow", name="Центральный административный округ")],
    )
    config = RuntimeSettings()
    consultant = Consultant.from_env() if consultant_enabled() else None

    @asynccontextmanager
    async def lifespan(app):
        service = PreviewService(registry, inventory, snapshot, transit)
        try:
            service.consultant = consultant
            app.state.services = service
            yield
        finally:
            await service.close_admission()
            if consultant is not None:
                await consultant.aclose()

    app = create_app(PreviewService(registry, inventory, snapshot, transit), lifespan=lifespan, secure_cookies=config.secure_cookies)

    @app.get("/cao")
    async def open_atlas():
        return RedirectResponse("/#atlas?analysis=" + ANALYSIS)

    return app
