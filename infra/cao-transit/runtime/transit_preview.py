"""Project the ЦАО transit export onto the stock atlas result contract."""
from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

TRANSIT = "cao-transit-500m"
TRANSIT_FILE = Path("/preview/transit-500m.json")
DESTINATIONS = Path("/app/data/bundles/moscow-urban-2026-11-10.4/destinations.json")


def transit_inventory(walk, labels, decode_line, moscow):
    if not TRANSIT_FILE.is_file():
        return None
    raw = TRANSIT_FILE.read_bytes()
    export = json.loads(raw)
    start = datetime.fromisoformat(export["date"]).replace(tzinfo=moscow)
    start += timedelta(seconds=export["start_seconds"])
    source = TRANSIT + "-" + sha256(raw).hexdigest()
    refs = [source]
    version = dict(walk["versions"], code_version=source)
    request = dict(walk["request"], passenger_mode="public_transport", departure_at=start)
    features = [item for item in walk["map_data"]["features"] if item["entity_kind"] != "destination"]
    metrics, scores, journeys = [], [], []
    destinations = defaultdict(set)
    base = {m["subject"]["point_id"] for m in walk["metrics"]}
    walking_journeys = {journey["journey_id"]: journey for journey in walk["journeys"]}
    penalty = export["transfer_penalty_seconds"]

    def metric(key, name, value, unit, subject, journey_id=None):
        return dict(metric_id=key, name=name, value=value, unit=unit,
                    status="available" if value is not None else "unknown",
                    reason_code="TRANSIT_SCOPE_ESTIMATE" if value is not None else "TRANSIT_ROUTE_UNAVAILABLE",
                    subject=subject, source_refs=refs, completeness="partial",
                    journey_id=journey_id, time_kind="complete_chain" if unit=="seconds" else None)

    for point in sorted(base):
        for category, label in labels.items():
            candidate = export["points"].get(point, {}).get(category)
            key = point + "-" + category
            subject = dict(kind="sample_point", point_id=point, category=category,
                           territory_id=request["area_id"], mode="public_transport")
            walk_option = walking_journeys.get(key + "-walk")
            walk_seconds = walk_option["total_seconds"] if walk_option else None
            walk_target = walk_option["destination_object_id"] if walk_option else None
            if candidate and (walk_seconds is None or candidate["walk_seconds"] < walk_seconds):
                walk_seconds = candidate["walk_seconds"]
                walk_target = candidate["destination_id"]
                walk_option = None
            transit_seconds = candidate["transit_seconds"] if candidate else None
            use_walk = walk_seconds is not None and (
                candidate is None or walk_seconds <= transit_seconds + candidate["transfers"] * penalty
            )
            if candidate is None and not use_walk:
                metrics.append(metric(key + "-transit", "best_observed_time", None, "seconds", subject))
                continue

            target = walk_target if use_walk else candidate["destination_id"]
            destinations[target].add(category)
            subject["destination_id"] = target
            journey_id = key + "-transit"
            legs = []
            now = start
            wait = first_wait = 0
            boarded = False
            current = point
            steps = candidate["legs"] if not use_walk else ()
            for index, step in enumerate(steps):
                mode = step["mode"]
                seconds = step["seconds"]
                end = now + timedelta(seconds=seconds)
                destination = target if index == len(steps)-1 else step["b"]
                if mode == "wait":
                    destination = current
                    wait += seconds
                    if not boarded:
                        first_wait += seconds
                    kind, service = ("wait_transfer" if boarded else "wait_initial"), None
                elif mode == "walk":
                    kind, service = "walk", "cao:walk"
                else:
                    kind = "ride"
                    service = f'cao:{mode}:{step["line"]}'
                    boarded = True
                shape = step.get("shape") or [point for item in step.get("encoded_shape", []) for point in decode_line(item)]
                if step.get("geometry") == "schematic":
                    service += ":schematic"
                leg = dict(mode="walk" if mode=="walk" else "public_transport", kind=kind,
                           from_node_id=current, to_node_id=destination, start_at=now, end_at=end,
                           duration_seconds=seconds, service_id=service, evidence_refs=refs)
                if len(shape) >= 2:
                    leg["geometry"] = dict(type="LineString", coordinates=shape, evidence_refs=refs)
                legs.append(leg)
                current = destination
                now = end
            travel = walk_seconds if use_walk else transit_seconds
            route_refs = refs
            if use_walk:
                now = start + timedelta(seconds=travel)
                if walk_option:
                    geometry = walk_option["legs"][0]["geometry"]
                    route_refs = [*refs, *walk_option["evidence_refs"]]
                else:
                    coordinates = [coordinate for encoded in candidate["walk_shape"]
                                   for coordinate in decode_line(encoded)]
                    geometry = dict(type="LineString", coordinates=coordinates, evidence_refs=refs)
                legs.append(dict(mode="walk", kind="walk", from_node_id=point, to_node_id=target,
                                 start_at=start, end_at=now, duration_seconds=travel, service_id="cao:walk",
                                 geometry=geometry, evidence_refs=route_refs))
            journey = dict(journey_id=journey_id, scope="urban", origin_node_id=point,
                           destination_node_id=target, point_id=point, destination_object_id=target,
                           requested_departure_at=start, departure_at=start, arrival_at=now,
                           total_seconds=travel, initial_wait_seconds=first_wait, total_wait_seconds=wait,
                           path_seconds=travel-first_wait, legs=legs, geometry=None, geometry_kind="none",
                           status="available", reason_code="TRANSIT_SCOPE_ESTIMATE", snapshot_id=request["snapshot_id"],
                           completeness="partial", traffic_mode="static", time_kind="complete_chain",
                           reference_id=None, evidence_refs=route_refs)
            journeys.append(journey)
            metrics.append(metric(key+"-transit", "best_observed_time", travel, "seconds", subject, journey_id))
            if candidate:
                transit_subject = dict(subject, destination_id=candidate["destination_id"])
                metrics.extend((
                    metric(key+"-walk", "Пешком до того же объекта", candidate["walk_seconds"], "seconds", transit_subject),
                    metric(key+"-pt-option", "Вариант на ОТ", transit_seconds, "seconds", transit_subject),
                    metric(key+"-transfers", "Пересадки в варианте на ОТ", float(candidate["transfers"]),
                           "transfers", transit_subject),
                ))
            scores.append(dict(
                score_id=key+"-estimate", name="observed_route_accessibility",
                value=100*2**(-travel/900), status="available", completeness="partial",
                methodology_version=version["methodology_version"], reason_code="OBSERVED_ROUTE_ESTIMATE",
                components=[dict(kind="metric",id=key+"-transit")],
                evidence_refs=refs, subject=subject, ranking_eligible=False,
            ))

    for row in json.loads(DESTINATIONS.read_bytes()):
        identity = row["destination_id"]
        if identity not in destinations:
            continue
        features.append(dict(entity_kind="destination", entity_id=identity, territory_id=request["area_id"],
                             geometry=dict(type="Point",coordinates=[row["lon"],row["lat"]]), geometry_kind="actual",
                             name=row["name"], categories=sorted(destinations[identity]),source_refs=refs))
    return dict(walk, analysis_id=TRANSIT, request=request, versions=version, journeys=journeys, scores=scores,
                metrics=metrics, map_data=dict(walk["map_data"],features=features),
                evidence_refs=[*walk["evidence_refs"],dict(evidence_id=source,source_id=TRANSIT,
                   url="https://transport.mos.ru/metro/map",retrieved_at=start,observed_at=None,section="pilot")],
                limitations=[
                    "Будний расчёт 13 ноября, 08:45. Сетка 500 м, поиск объектов 2 км, до 4 км для пустых категорий.",
                    "Автобусы и трамваи — GTFS. Метро и МЦК — время pMetro и ожидание в половину интервала.",
                    "Для части линий метро интервал оценочный. Наземные линии между остановками и часть переходов показаны пунктиром как схема.",
                    "Сравниваем полный пеший путь с ОТ. При выборе добавляем к оценке ОТ 4 минуты за пересадку.",
                    "Максимум 3 пересадки. Данные за другие часы не рассчитаны.",
                ])
