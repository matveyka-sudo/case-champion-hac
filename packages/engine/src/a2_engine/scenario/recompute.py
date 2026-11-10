"""Full selected-scope recomputation through existing solvers and aggregators."""

from typing import Literal, cast

from a2_engine.freight.delivery.search import analyze_intercity_cargo
from a2_engine.freight.urban_analysis import analyze_urban_cargo
from a2_engine.intercity.analysis.calculation import analyze_intercity_passenger
from a2_engine.intercity.analysis.identity import normalize_physical_services
from a2_engine.pipeline.analysis import analyze_pipeline_connections
from a2_engine.routing.timetable import check_limit
from a2_engine.scenario.models import (
    ScenarioComputation,
    ScenarioInputs,
    ScenarioMeasure,
    UrbanScenarioResult,
)
from a2_engine.shared.results import MetricResult
from a2_engine.urban.analysis.calculation import analyze_urban_candidates
from a2_engine.urban.districts.calculation import analyze_urban_districts
from a2_engine.urban.transit.journeys import check_urban_inputs
from a2_engine.urban.transit.last_return import search_urban_last_return
from a2_engine.urban.transit.models import UrbanTransitNetwork
from a2_engine.urban.transit.search import search_urban_transit


def recompute(inputs: ScenarioInputs, fixed_hash: str) -> ScenarioComputation:
    datasets = {t.network_id: t for t in inputs.timetables}
    metrics: list[ScenarioMeasure] = []
    urban_results = []
    intercity_results = []
    cargo_results = []
    urban_cargo_results = []
    pipeline_results = []

    def metric(
        key: tuple[str, ...],
        unit: str,
        higher: bool,
        result: MetricResult,
        proven: bool = True,
        route_id: str | None = None,
    ) -> None:

        absent = result.reason_code == "NO_FEASIBLE_ROUTE"
        state: Literal["known", "absent", "unknown"] = (
            "absent" if absent and proven else "known" if result.value is not None else "unknown"
        )
        metrics.append(
            ScenarioMeasure(
                key,
                cast(Literal["seconds", "index", "count", "utc_seconds", "percent"], unit),
                higher,
                result.value,
                state,
                proven and state != "unknown",
                fixed_hash,
                route_id,
                result.reason_code,
            )
        )

    def time_metric(
        key: tuple[str, ...],
        value: float | None,
        *,
        proven: bool,
        absent: bool,
        route_id: str | None = None,
    ) -> None:
        state = "absent" if absent and proven else "known" if value is not None else "unknown"

        metrics.append(
            ScenarioMeasure(
                key,
                "seconds",
                False,
                value,
                cast(Literal["known", "absent", "unknown"], state),
                proven and state != "unknown",
                fixed_hash,
                route_id,
                "NO_FEASIBLE_ROUTE" if state == "absent" else None,
            )
        )

    for job in inputs.urban:
        if job.network_id not in datasets:
            raise ValueError("SCENARIO_TIMETABLE_DATASET_MISSING")
        dataset = datasets[job.network_id]
        urban_table = dataset.timetable
        if dataset.physical_services:
            from dataclasses import replace

            normalized, _, _ = normalize_physical_services(
                dataset.timetable, dataset.transport_modes, dataset.physical_services
            )
            original_runs = {r.run_id: r for r in dataset.timetable.runs}
            types = {
                group.physical_service_id: original_runs[group.run_ids[0]].transport_type
                for group in dataset.physical_services
            }
            urban_table = replace(
                normalized,
                runs=tuple(
                    replace(r, transport_type=types.get(r.trip_id, r.transport_type))
                    for r in normalized.runs
                ),
            )
        network = UrbanTransitNetwork(
            job.context, urban_table, dataset.timetable.scope.evidence_refs
        )
        searches = []
        candidates = list(job.fixed_candidates)
        computed_searches: dict[tuple[str, str, str], list[bool]] = {}
        checked_points: set[str] = set()
        checked_destinations: set[str] = set()
        missing_boundary = False
        for point in job.points:
            for passenger_mode in job.modes:
                for category_name, _ in job.selected_categories:
                    key = point.point_id, category_name, passenger_mode
                    computed_searches[key] = []
                    if passenger_mode not in ("public_transport", "walk"):
                        continue
                    for destination in job.destinations:
                        if (
                            category_name not in destination.categories
                            or not destination.in_service_scope
                        ):
                            continue
                        if (
                            point.origin_node_id not in urban_table.scope.node_ids
                            or (destination.physical_node_id or destination.access_node_id)
                            not in urban_table.scope.node_ids
                        ):
                            # Missing data does not excuse malformed inputs. Each
                            # endpoint needs validation only once across skipped pairs.
                            if (
                                point.point_id not in checked_points
                                or destination.destination_id not in checked_destinations
                            ):
                                check_urban_inputs(network, point, destination, job.rules)
                                check_limit(job.resource_limit)
                                if not (
                                    urban_table.scope.start_at
                                    <= job.context.requested_departure_at
                                    <= urban_table.scope.end_at
                                ):
                                    raise ValueError("DEPARTURE_OUTSIDE_TIMETABLE_SCOPE")
                                checked_points.add(point.point_id)
                                checked_destinations.add(destination.destination_id)
                            computed_searches[key].append(False)
                            missing_boundary = True
                            continue
                        search_result = search_urban_transit(
                            network,
                            point,
                            destination,
                            category=category_name,
                            rules=job.rules,
                            passenger_mode=cast(
                                Literal["public_transport", "walk"], passenger_mode
                            ),
                            resource_limit=job.resource_limit,
                        )
                        searches.append(search_result)
                        computed_searches[key].append(search_result.search_complete)
                        if search_result.best_known is not None:
                            candidates.append(search_result.best_known.candidate)
        # Existing coverage certifies the catalogue. Routing completeness is recomputed.
        from dataclasses import replace

        coverage = tuple(
            replace(
                item,
                search_complete=(
                    all(
                        computed_searches.get(
                            (item.scope.point_id, item.scope.category, item.scope.passenger_mode),
                            (),
                        )
                    )
                    and not item.provider_error
                    and dataset.timetable.scope.schedule_complete
                    and dataset.timetable.scope.calendar_complete
                    and dataset.timetable.scope.footpaths_complete
                    and dataset.timetable.scope.transfers_complete
                    and dataset.timetable.scope.continuations_complete
                ),
            )
            if item.scope.passenger_mode in ("public_transport", "walk")
            else item
            for item in job.coverage
        )
        analysis = analyze_urban_candidates(
            job.context,
            job.points,
            job.destinations,
            tuple(candidates),
            coverage,
            area=job.area,
            selected_categories=dict(job.selected_categories),
            modes=job.modes,
            population_coverage=job.population_coverage,
            config=job.config,
            rules=job.rules,
            thresholds=job.thresholds,
        )
        if missing_boundary:
            analysis = replace(
                analysis,
                limitations=tuple(
                    sorted(set(analysis.limitations) | {"PHYSICAL_BOUNDARY_CONNECTOR_UNAVAILABLE"})
                ),
            )
        districts = (
            analyze_urban_districts(
                analysis,
                job.original_cells,
                job.districts,
                job.district_shares,
                job.district_coverage,
            )
            if job.district_coverage
            else None
        )
        returns = []
        for urban_mode in analysis.modes:
            metric(
                (
                    job.job_id,
                    "territory",
                    job.context.territory_id,
                    "all",
                    urban_mode.passenger_mode,
                    "index",
                ),
                "index",
                True,
                urban_mode.index,
                urban_mode.completeness == "full",
            )
            for point_result in urban_mode.points:
                point = point_result.point
                metric(
                    (
                        job.job_id,
                        "point",
                        point.point_id,
                        "all",
                        urban_mode.passenger_mode,
                        "index",
                    ),
                    "index",
                    True,
                    point_result.index,
                    point_result.completeness == "full",
                )
                for category_result in point_result.categories:
                    prefix = (
                        job.job_id,
                        "point",
                        point.point_id,
                        category_result.category,
                        urban_mode.passenger_mode,
                    )
                    chosen_object = category_result.best
                    route_id = chosen_object.journey.candidate.journey_id if chosen_object else None
                    time_metric(
                        prefix + ("time",),
                        chosen_object.journey.total_seconds if chosen_object else None,
                        proven=category_result.minimum_proven,
                        absent=chosen_object is None,
                        route_id=route_id,
                    )
                    metric(
                        prefix + ("index",),
                        "index",
                        True,
                        category_result.index,
                        category_result.minimum_proven,
                        route_id,
                    )
                    metric(
                        prefix + ("count",),
                        "count",
                        True,
                        category_result.count,
                        category_result.minimum_proven,
                    )
                    if job.return_day is not None and urban_mode.passenger_mode in (
                        "walk",
                        "public_transport",
                    ):
                        returned = (
                            search_urban_last_return(
                                network,
                                point,
                                chosen_object.destination,
                                local_day=job.return_day,
                                rules=job.rules,
                                passenger_mode=cast(
                                    Literal["public_transport", "walk"], urban_mode.passenger_mode
                                ),
                                resource_limit=job.resource_limit,
                            )
                            if chosen_object
                            else None
                        )
                        returns.append(
                            (
                                (
                                    point.point_id,
                                    category_result.category,
                                    urban_mode.passenger_mode,
                                ),
                                returned,
                            )
                        )
                        value = (
                            returned.leave_at.timestamp()
                            if returned is not None and returned.leave_at is not None
                            else None
                        )
                        proven = (
                            returned.proven
                            if returned is not None
                            else category_result.minimum_proven
                        )
                        state = "known" if value is not None else "absent" if proven else "unknown"

                        metrics.append(
                            ScenarioMeasure(
                                prefix + ("last_return",),
                                "utc_seconds",
                                True,
                                value,
                                cast(Literal["known", "absent", "unknown"], state),
                                proven,
                                fixed_hash,
                            )
                        )
        if districts is not None:
            for mode_result in districts.modes:
                for district_metric in (mode_result.overall,) + mode_result.categories:
                    category_name = district_metric.scope.category or "all"
                    for district in district_metric.districts:
                        metric(
                            (
                                job.job_id,
                                "district",
                                district.district_id,
                                category_name,
                                mode_result.passenger_mode,
                                "index",
                            ),
                            "index",
                            True,
                            district.index,
                        )
                    metric(
                        (
                            job.job_id,
                            "territory",
                            job.context.territory_id,
                            category_name,
                            mode_result.passenger_mode,
                            "district_gap",
                        ),
                        "index",
                        False,
                        district_metric.gap,
                    )
        urban_results.append(
            UrbanScenarioResult(job.job_id, analysis, tuple(searches), districts, tuple(returns))
        )
    for intercity in inputs.intercity:
        dataset = datasets[intercity.network_id]
        intercity_result = analyze_intercity_passenger(
            intercity.scope,
            intercity.requested_at,
            dataset.timetable,
            dataset.transport_modes,
            dataset.physical_services,
            intercity.reference,
            config=intercity.config,
            rules=intercity.rules,
            frequency_start_at=intercity.frequency_start_at,
            frequency_end_at=intercity.frequency_end_at,
            readiness=intercity.readiness,
            car=intercity.car,
            resource_limit=intercity.resource_limit,
        )
        intercity_results.append((intercity.job_id, intercity_result))
        for intercity_category in intercity_result.by_mode + (intercity_result.public,):
            prefix = (
                intercity.job_id,
                "pair",
                intercity.scope.comparison_scope.origin_territory_id,
                intercity.scope.comparison_scope.destination_territory_id,
                intercity_category.category,
            )
            chosen_chain = intercity_category.best_known
            routes = [
                c
                for c in intercity_result.candidates
                if intercity_category.category == "public"
                or c.category == intercity_category.category
            ]
            absent = (
                intercity_result.timetable_complete
                and intercity_result.modes_complete
                and intercity_result.search.interrupted is False
                and not routes
            )
            score = (
                chosen_chain.evaluation.index
                if chosen_chain
                else MetricResult(0, "available", "NO_FEASIBLE_ROUTE")
                if absent
                else MetricResult(None, "unknown", "INDEX_WINNER_UNAVAILABLE")
            )
            metric(
                prefix + ("index",),
                "index",
                True,
                score,
                intercity_category.optimum_proven or absent,
            )
            for name in ("path_seconds", "initial_wait_seconds", "total_seconds"):
                time_metric(
                    prefix + (name,),
                    getattr(chosen_chain, name) if chosen_chain else None,
                    proven=intercity_category.optimum_proven or absent,
                    absent=absent,
                    route_id=chosen_chain.route.chain_id if chosen_chain else None,
                )
        for frequency in intercity_result.frequency:
            metric(
                (
                    intercity.job_id,
                    "pair",
                    intercity.scope.comparison_scope.origin_territory_id,
                    intercity.scope.comparison_scope.destination_territory_id,
                    frequency.category,
                    "departures",
                ),
                "count",
                True,
                MetricResult(frequency.count, "available")
                if frequency.count is not None
                else MetricResult(None, "unknown", "FREQUENCY_INCOMPLETE"),
                frequency.complete,
            )
    for cargo in inputs.cargo:
        cargo_result = analyze_intercity_cargo(
            cargo.scope,
            cargo.started_at,
            cargo.profile,
            cargo.states,
            cargo.actions,
            cargo.readiness,
            config=cargo.config,
            rules=cargo.rules,
            resource_limit=cargo.resource_limit,
        )
        cargo_results.append((cargo.job_id, cargo_result))
        for cargo_category in cargo_result.by_mode + (cargo_result.overall,):
            prefix = (
                cargo.job_id,
                "cargo_pair",
                cargo.scope.origin_territory_id,
                cargo.scope.destination_territory_id,
                cargo_category.category,
            )
            chosen_delivery = cargo_category.best_known
            absent = cargo_category.optimum_proven and chosen_delivery is None
            index = (
                chosen_delivery.evaluation.index
                if chosen_delivery
                else MetricResult(0, "available", "NO_FEASIBLE_ROUTE")
                if absent
                else MetricResult(None, "unknown", "CARGO_WINNER_UNKNOWN")
            )
            metric(prefix + ("index",), "index", True, index, cargo_category.optimum_proven)
            time_metric(
                prefix + ("delivery_time",),
                chosen_delivery.evaluation.total_seconds.value if chosen_delivery else None,
                proven=cargo_category.optimum_proven,
                absent=absent,
                route_id=chosen_delivery.path_id if chosen_delivery else None,
            )
    for urban_cargo in inputs.urban_cargo:
        urban_cargo_result = analyze_urban_cargo(
            urban_cargo.profile,
            urban_cargo.requested_at,
            urban_cargo.sites,
            urban_cargo.nodes,
            urban_cargo.routes,
            urban_cargo.receipts,
            urban_cargo.coverage,
            urban_cargo.selected_node_types,
            urban_cargo.area_km2,
            config=urban_cargo.config,
        )
        urban_cargo_results.append((urban_cargo.job_id, urban_cargo_result))
        for group in urban_cargo_result.by_type:
            metric(
                (
                    urban_cargo.job_id,
                    "territory",
                    "physical_sites",
                    group.scope.node_type,
                    group.scope.direction,
                    "index",
                ),
                "index",
                True,
                group.index,
                group.full_ranking_eligible,
            )
            for site in group.sites:
                prefix = (
                    urban_cargo.job_id,
                    "site",
                    site.site_id,
                    group.scope.node_type,
                    group.scope.direction,
                )
                metric(
                    prefix + ("index",), "index", True, site.evaluation.index, site.minimum_proven
                )
                time_metric(
                    prefix + ("time",),
                    site.best_known_route.seconds if site.best_known_route else None,
                    proven=site.minimum_proven,
                    absent=site.best_known_route is None,
                    route_id=site.best_known_route.route_id if site.best_known_route else None,
                )
    for pipeline in inputs.pipelines:
        pipeline_result = analyze_pipeline_connections(
            pipeline.scope, pipeline.observations, config=pipeline.config
        )
        pipeline_results.append((pipeline.job_id, pipeline_result))
        # Stable query metric survives empty results; individual connection quantities
        # remain inside the rich result and are frozen by the operation allowlist.
        metric(
            (
                pipeline.job_id,
                "pipeline",
                pipeline.scope.origin_territory_id,
                pipeline.scope.destination_territory_id,
                pipeline.scope.product,
                "connections",
            ),
            "count",
            True,
            MetricResult(
                sum(r.state == "connected" for r in pipeline_result.connections), "available"
            )
            if pipeline_result.connectivity_complete
            else MetricResult(None, "unknown", "PIPELINE_COVERAGE_INCOMPLETE"),
            pipeline_result.connectivity_complete,
        )
        for connection in pipeline_result.connections:
            metric(
                (
                    pipeline.job_id,
                    "pipeline_record",
                    connection.observation.connection.connection_id,
                    pipeline.scope.period,
                    pipeline.scope.product,
                    "utilization",
                ),
                "percent",
                False,
                connection.utilization.utilization_percent,
            )
    keys = [m.key for m in metrics]
    if len(set(keys)) != len(keys):
        raise ValueError("DUPLICATE_SCENARIO_METRIC_SEMANTICS")
    return ScenarioComputation(
        tuple(urban_results),
        tuple(intercity_results),
        tuple(cargo_results),
        tuple(urban_cargo_results),
        tuple(pipeline_results),
        tuple(sorted(metrics, key=lambda m: m.key)),
    )
