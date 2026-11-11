"""Server template identity is independent from the operation enum."""

import pytest
from a2_api.core.jobs import V2JobContext
from a2_contracts.api import JobAccepted
from a2_contracts.common import canonical_hash
from a2_contracts.v2.scenarios import ScenarioProposal, ScenarioRunOptions
from test_v2_tools import ScopedSavedService


class ProposalRepository:
    def __init__(self, saved, proposal):
        self.saved, self.proposal = saved, proposal
        self.context = V2JobContext(
            schema_version="2",
            snapshot_id=saved.snapshot_id,
            snapshot_hash="synthetic-hash",
            versions=saved.versions,
            analysis_id=saved.analysis_id,
            normalized_request=saved.request,
            access_scope="owner",
        )
        self.command = None

    async def get_proposal(self, scenario_id, scope):
        assert scope == "owner"
        return self.proposal

    async def replay(self, *args, **kwargs):
        return None

    async def get_run_parameters(self, *args, **kwargs):
        return {"context": self.context.model_dump(mode="json")}

    async def enqueue(self, kind, request, context, key, request_id, scope):
        self.command = request
        return JobAccepted(job_id="synthetic-job", status="queued")


@pytest.mark.asyncio
async def test_registered_versioned_template_runs_without_matching_operation_enum():
    service = ScopedSavedService()
    saved = service.saved
    proposal = ScenarioProposal(
        schema_version="2",
        scenario_id="server-proposal",
        baseline_analysis_id=saved.analysis_id,
        template_id="coordinate-transfer-v1",
        operation_type="coordinate_transfer",
        fixed_parameters=dict(
            service_ids=["service-1"],
            shift_seconds=60,
            demand_evidence=[],
            resource_evidence=[],
            turnaround_evidence=[],
            resource_constraints=[],
        ),
        evidence_refs=[],
        assumptions=[],
        status="proposed",
        feasibility="unverified",
        versions=saved.versions,
        baseline_result_hash=saved.result_hash,
        editable_local_graph=True,
        limitations=["TURNAROUND_UNVERIFIED"],
    )
    repository = ProposalRepository(saved, proposal)
    repository.context.snapshot_hash = canonical_hash(
        service.registry.snapshots[saved.snapshot_id].model_dump(mode="json")
    )
    service.repository = repository
    service.code_version = "demo-code"
    service.engine_enabled = True
    service.registered_template_ids = frozenset({"coordinate-transfer-v1"})
    accepted = await service.scenario(
        proposal.scenario_id, ScenarioRunOptions(schema_version="2"), "key", "test-request", "owner"
    )
    assert accepted.status == "queued"
    assert repository.command.scenario_id == "server-proposal"
