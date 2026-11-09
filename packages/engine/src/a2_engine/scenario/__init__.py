"""Versioned, allowlisted scenario recomputation. No IO or baseline mutation."""

from a2_engine.scenario.effects import compare_scenario_metrics, validate_effect_coverage
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.feasibility import check_scenario_feasibility
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    CargoExecution,
    CargoScenarioInput,
    IntercityScenarioInput,
    PipelineScenarioInput,
    ScenarioBaseline,
    ScenarioComputation,
    ScenarioEffect,
    ScenarioEventChange,
    ScenarioFeasibility,
    ScenarioFeasibilityInputs,
    ScenarioImpact,
    ScenarioInputs,
    ScenarioMeasure,
    ScenarioOperation,
    ScenarioOverlay,
    ScenarioResourceBlock,
    ScenarioResourceLink,
    ScenarioResult,
    ScenarioTimetable,
    UrbanCargoScenarioInput,
    UrbanScenarioInput,
    UrbanScenarioResult,
)
from a2_engine.scenario.overlay import apply_scenario_overlay, execution_hash

__all__ = [
    "CargoExecution",
    "CargoScenarioInput",
    "IntercityScenarioInput",
    "PipelineScenarioInput",
    "ScenarioBaseline",
    "ScenarioComputation",
    "ScenarioEffect",
    "ScenarioEventChange",
    "ScenarioFeasibility",
    "ScenarioFeasibilityInputs",
    "ScenarioImpact",
    "ScenarioInputs",
    "ScenarioMeasure",
    "ScenarioOperation",
    "ScenarioOverlay",
    "ScenarioResourceBlock",
    "ScenarioResourceLink",
    "ScenarioResult",
    "ScenarioTimetable",
    "UrbanCargoScenarioInput",
    "UrbanScenarioInput",
    "UrbanScenarioResult",
    "create_scenario_baseline",
    "simulate_scenario",
    "validate_scenario_result",
    "compare_scenario_metrics",
    "validate_effect_coverage",
    "check_scenario_feasibility",
    "scenario_hash",
    "apply_scenario_overlay",
    "execution_hash",
]
