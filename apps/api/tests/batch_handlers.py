"""TEST_ONLY spawned handlers. No provider calls or production registrations."""

import time

from a2_api.core.batches import BatchCheckpoint, PairObservation
from a2_contracts.common import canonical_hash
from a2_contracts.v2.results import AnalysisResult, EvidenceRef, Metric, Score, SubjectRef
from a2_data_pipeline.contract_v2 import load_accepted_data
from a2_engine import WalkResponseEnvelope, load_urban_config, select_h_seconds


def result(claim):
    context = claim.context
    snapshot = load_accepted_data(context.snapshot_id)
    territory = next(
        t for t in snapshot.territories if t.territory_id == context.normalized_request.city_id
    )
    h = select_h_seconds(territory.area_km2, bands=load_urban_config().area_h_bands)
    subject = SubjectRef(kind="territory", territory_id=territory.territory_id, mode="walk")
    evidence = [
        EvidenceRef(
            evidence_id=e.evidence_id,
            source_id=e.source_id,
            url=e.url,
            retrieved_at=e.retrieved_at,
            observed_at=None,
            section="TEST_ONLY",
        )
        for e in snapshot.evidence
    ]
    saved = AnalysisResult(
        schema_version="2",
        analysis_id=context.analysis_id,
        analysis_kind="urban_passenger",
        request=context.normalized_request,
        snapshot_id=context.snapshot_id,
        methodology_version=context.versions.methodology_version,
        config_hash=context.versions.config_hash,
        traffic_mode="static",
        status="partial",
        metrics=[
            Metric(
                metric_id="TEST_ONLY-h",
                name="urban_h_seconds",
                value=h.value,
                unit="seconds",
                status=h.status,
                reason_code=h.reason_code,
                source_refs=territory.source_refs,
                subject=subject,
                completeness="full",
            )
        ],
        scores=[
            Score(
                score_id="TEST_ONLY-index",
                name="urban_index",
                value=None,
                status="unknown",
                completeness="partial",
                methodology_version=context.versions.methodology_version,
                reason_code="TEST_ONLY_TIMEOUT",
                components=[],
                evidence_refs=[],
                subject=subject,
                ranking_eligible=False,
            )
        ],
        evidence_refs=evidence,
        limitations=["TEST_ONLY_TIMEOUT"],
        versions=context.versions,
        journeys=[],
        references=[],
        counts=[],
        last_returns=[],
        result_hash="TEST_ONLY-pending",
    )
    return saved.model_copy(
        update={
            "result_hash": canonical_hash(saved.model_dump(mode="json", exclude={"result_hash"}))
        }
    )


def success(claim, channel):
    execution = claim.context.batch_execution
    done = {c.batch_id for c in channel.completed}
    for batch_id, pairs in execution.batches:
        if batch_id in done:
            continue
        observations = []
        for pair in pairs:
            r = pair.request
            envelope = WalkResponseEnvelope(
                r.request_id,
                r.point_id,
                r.destination_id,
                r.build,
                r.payload_hash,
                r.origin.correlation_id,
                r.destination.correlation_id,
                "timeout",
                None,
                (),
            )
            observations.append(
                PairObservation(
                    pair_id=pair.pair_id, envelope=envelope, evidence=[], raw_observations=[]
                )
            )
        channel.commit(
            BatchCheckpoint(
                schema_version="waydex-walk-checkpoint/1",
                execution_identity=execution.identity,
                batch_id=batch_id,
                observations=observations,
            )
        )
    return result(claim)


def omit(claim, channel):
    return result(claim)


def malformed(claim, channel):
    channel.connection.send_bytes(b"OD1:{}")
    return result(claim)


def oversized(claim, channel):
    channel.connection.send_bytes(b"OD1:" + b"x" * (8 * 1024 * 1024 + 1))
    return result(claim)


def hang(claim, channel):
    while True:
        time.sleep(0.05)
