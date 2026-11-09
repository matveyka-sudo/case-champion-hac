"""Unique original cells and physical sites, with explicit unknown lower bounds."""

from math import fsum

from a2_engine.scenario.models import ScenarioEffect, ScenarioImpact, ScenarioInputs


def scenario_impact(inputs: ScenarioInputs, effects: tuple[ScenarioEffect, ...]) -> ScenarioImpact:
    effect_by_point: dict[tuple[str, str], list[ScenarioEffect]] = {}
    effect_by_job: dict[str, list[ScenarioEffect]] = {}
    for effect in effects:
        effect_by_job.setdefault(effect.key[0], []).append(effect)
        if effect.key[1] == "point":
            effect_by_point.setdefault((effect.key[0], effect.key[2]), []).append(effect)
    populations: dict[tuple[str, str, str], float | None] = {}
    partial_cells = set()
    affected_cells = set()
    unknown_cells = set()
    population_complete = True
    changed = {"improved", "worsened", "new_connection", "lost_connection"}
    for job in inputs.urban:
        if not job.population_coverage.complete or job.population_coverage.excluded_population != 0:
            population_complete = False
        cell_records = {cell.cell_id: cell.population for cell in job.original_cells}
        if not cell_records:
            point_records = {p.point_id: p for p in job.points}
            for point in point_records.values():
                if point.cell_id not in cell_records:
                    cohort = [p for p in point_records.values() if p.cell_id == point.cell_id]
                    cell_records[point.cell_id] = (
                        fsum(p.population for p in cohort if p.population is not None)
                        if all(p.population is not None for p in cohort)
                        else None
                    )
        for point in job.points:
            key = (
                job.context.city_id,
                point.cell_id,
                job.context.versions.population_version or "unknown",
            )
            if not job.original_cells and any(
                p.cell_id == point.cell_id and p.population_share_id is not None for p in job.points
            ):
                partial_cells.add(key)
            population = cell_records.get(point.cell_id)
            if key in populations and populations[key] != population:
                raise ValueError("SCENARIO_CELL_POPULATION_BASIS_CONFLICT")
            populations[key] = population
            entries = effect_by_point.get((job.job_id, point.point_id), ())
            if any(e.outcome in changed for e in entries):
                affected_cells.add(key)
            elif any(e.outcome == "unknown" for e in entries):
                unknown_cells.add(key)
    unknown_cells -= affected_cells
    unknown_cells.update(
        key for key in affected_cells if populations[key] is None or key in partial_cells
    )
    if unknown_cells:
        population_complete = False
    affected_sites = set()
    unknown_sites = set()
    unmapped = set()
    for cargo_job in inputs.urban_cargo:
        for site_id in cargo_job.coverage.expected_site_ids:
            entries = [
                e
                for e in effect_by_job.get(cargo_job.job_id, ())
                if e.key[1] == "site" and e.key[2] == site_id
            ]
            if any(e.outcome in changed for e in entries):
                affected_sites.add(site_id)
            elif any(e.outcome == "unknown" for e in entries):
                unknown_sites.add(site_id)
    for cargo in inputs.cargo:
        entries = effect_by_job.get(cargo.job_id, ())
        has_change = any(e.outcome in changed for e in entries)
        has_unknown = any(e.outcome == "unknown" for e in entries)
        if has_change or has_unknown:
            if not cargo.physical_sites:
                unmapped.add(cargo.job_id)
            elif has_change:
                affected_sites.update(s.site_id for s in cargo.physical_sites)
            else:
                unknown_sites.update(s.site_id for s in cargo.physical_sites)
    unknown_sites -= affected_sites
    sites_complete = (
        not unknown_sites
        and not unmapped
        and all(j.coverage.roster_complete for j in inputs.urban_cargo)
    )
    population = fsum(value for key in affected_cells if (value := populations[key]) is not None)
    return ScenarioImpact(
        population,
        tuple(sorted(affected_cells)),
        tuple(sorted(unknown_cells)),
        population_complete,
        tuple(sorted(affected_sites)),
        tuple(sorted(unknown_sites)),
        sites_complete,
        tuple(sorted(unmapped)),
        tuple(sorted(effect_by_job)),
    )
