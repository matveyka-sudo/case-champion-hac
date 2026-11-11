"""Compatibility exports; implementations live in concrete submodules."""

from a2_engine.urban.walk.analysis import (
    analyze_normalized_walk_routes as analyze_normalized_walk_routes,
)
from a2_engine.urban.walk.analysis import analyze_walk_responses as analyze_walk_responses
from a2_engine.urban.walk.analysis import plan_walk_routes as plan_walk_routes
from a2_engine.urban.walk.inputs import _active as _active
from a2_engine.urban.walk.inputs import _coordinate as _coordinate
from a2_engine.urban.walk.inputs import _table as _table
from a2_engine.urban.walk.inputs import prepare_walk_inputs as prepare_walk_inputs
from a2_engine.urban.walk.models import WalkAnalysisResult as WalkAnalysisResult
from a2_engine.urban.walk.models import WalkCatalog as WalkCatalog
from a2_engine.urban.walk.models import WalkEndpoint as WalkEndpoint
from a2_engine.urban.walk.models import WalkInputs as WalkInputs
from a2_engine.urban.walk.models import WalkPlanIssue as WalkPlanIssue
from a2_engine.urban.walk.models import WalkRoutePlan as WalkRoutePlan

__all__ = [
    "WalkEndpoint",
    "WalkCatalog",
    "WalkInputs",
    "WalkPlanIssue",
    "WalkRoutePlan",
    "WalkAnalysisResult",
    "_coordinate",
    "_table",
    "_active",
    "prepare_walk_inputs",
    "plan_walk_routes",
    "analyze_walk_responses",
    "analyze_normalized_walk_routes",
]
