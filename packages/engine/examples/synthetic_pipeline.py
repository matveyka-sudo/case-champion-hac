"""Synthetic accepted oil connection, infrastructure utilization above 100%."""

from datetime import UTC, datetime, timedelta

from a2_engine.pipeline.analysis import (
    PipelineAnalysisScope,
    PipelineConnection,
    PipelineConnectionsAnalysis,
    PipelineContext,
    PipelineEndpoint,
    PipelineMeasurement,
    PipelineObservation,
    analyze_pipeline_connections,
)
from a2_engine.pipeline.utilization import PipelineBasis
from a2_engine.shared.config import load_freight_config

START = datetime(2026, 11, 1, tzinfo=UTC)
END = START + timedelta(days=30)
PROOF = ("synthetic-proof",)
CONTEXT = PipelineContext("snapshot", "pipeline-registry-graph", "registry-v1")


def demo_inputs() -> tuple[PipelineAnalysisScope, PipelineObservation]:
    scope = PipelineAnalysisScope(
        CONTEXT,
        "territory-A",
        "territory-B",
        ("A",),
        ("B",),
        "oil",
        "2026-11",
        START,
        END,
        True,
        PROOF,
    )
    origin = PipelineEndpoint(
        "A", "territory-A", "oil", CONTEXT, "intake", START, END, "confirmed", PROOF
    )
    target = PipelineEndpoint(
        "B", "territory-B", "oil", CONTEXT, "delivery", START, END, "confirmed", PROOF
    )
    connection = PipelineConnection(
        "connection", "oil", CONTEXT, origin, target, "confirmed", PROOF
    )
    basis = PipelineBasis("oil", "2026-11", "tonnes", "connection", "A->B")
    volume = PipelineMeasurement(120, basis, CONTEXT, START, END, PROOF)
    capacity = PipelineMeasurement(100, basis, CONTEXT, START, END, PROOF)
    return scope, PipelineObservation(connection, volume, capacity)


def run_demo() -> PipelineConnectionsAnalysis:
    scope, observation = demo_inputs()
    result = analyze_pipeline_connections(scope, (observation,), config=load_freight_config())
    assert result.state == "connected" and result.connectivity_complete
    assert result.connections[0].utilization.utilization_percent.value == 120
    assert result.connections[0].utilization.diagnostics == ("UTILIZATION_ABOVE_100",)
    return result


if __name__ == "__main__":
    print(
        "SYNTHETIC pipeline: oil A->B, U=120% with QA; no commercial capacity claim",
        run_demo().state,
    )
