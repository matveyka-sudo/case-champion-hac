"""TEST_ONLY child handlers: compact immutable progress, no provider or SQL calls."""

import time

from a2_api.core.batches import BatchCheckpoint, PairObservation
from a2_api.core.walk_route_artifacts import RouteArtifactContext, write_route_artifact
from a2_engine import WalkResponseEnvelope
from batch_handlers import result


def _execute(claim, channel, *, interrupt=False):
    execution = claim.context.batch_execution
    authority = RouteArtifactContext(execution)
    done = {checkpoint.batch_id for checkpoint in channel.completed}
    for batch_id, pairs in execution.batches:
        if batch_id in done:
            continue
        refs = []
        for pair in pairs:
            request = pair.request
            observation = PairObservation(
                pair_id=pair.pair_id,
                envelope=WalkResponseEnvelope(
                    request.request_id,
                    request.point_id,
                    request.destination_id,
                    request.build,
                    request.payload_hash,
                    request.origin.correlation_id,
                    request.destination.correlation_id,
                    "timeout",
                    None,
                    (),
                ),
                evidence=[],
                raw_observations=[],
            )
            refs.append(write_route_artifact(observation, authority))
        channel.commit(
            BatchCheckpoint(
                schema_version="waydex-walk-checkpoint/2",
                execution_identity=authority.identity,
                batch_id=batch_id,
                observations=refs,
            )
        )
        if interrupt:
            while True:
                time.sleep(0.05)
    return result(claim)


def success(claim, channel):
    return _execute(claim, channel)


def hang_after_commit(claim, channel):
    return _execute(claim, channel, interrupt=True)
