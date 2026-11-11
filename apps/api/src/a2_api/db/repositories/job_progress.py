"""One bounded MVCC read of admitted persisted batches; no raw/file validation.

The SQL hash recipe mirrors BatchPlan.batches in core/batches.py. Pair IDs and
execution identities are validated lowercase ASCII hex, so its literal JSON is
exactly canonical_hash's sorted, compact, UTF-8 JSON. Parity tests against the
actual BatchExecution.batches property are mandatory when that recipe changes;
this is not an independent execution identity or a source-validity assertion.
"""

from typing import TYPE_CHECKING, Literal

from a2_contracts.api import Job
from a2_contracts.v2.api import BatchProgress

from a2_api.core.errors import DomainError
from a2_api.db.rows import job_from_row

if TYPE_CHECKING:
    from a2_api.db.repositories.jobs import JobRepository

JOB_PROGRESS_SQL = """
WITH owned AS MATERIALIZED (
    SELECT j.* FROM jobs j
    WHERE j.job_id=$1 AND j.schema_version='2' AND j.access_scope=$2
), admitted AS MATERIALIZED (
    SELECT j.job_id,j.status,j.finished_at,a.execution_identity,e.payload AS plan
    FROM owned j
    JOIN od_attempts a ON a.job_id=j.job_id AND a.access_scope=j.access_scope
        AND a.claim_token=j.claim_token
    JOIN od_executions e ON e.access_scope=a.access_scope
        AND e.execution_identity=a.execution_identity
        AND e.payload=j.payload#>'{context,batch_execution}'
    WHERE j.kind='analysis' AND j.status<>'queued' AND j.claim_token<>'cache'
        AND a.execution_identity ~ '^[a-f0-9]{64}$'
        AND e.payload->>'access_scope'=j.access_scope
), bounded AS MATERIALIZED (
    SELECT a.*,
        CASE WHEN jsonb_typeof(plan->'pairs')='array'
            THEN jsonb_array_length(plan->'pairs') ELSE 0 END AS pair_count,
        CASE WHEN jsonb_typeof(plan->'batch_size')='number'
            AND plan->>'batch_size' ~ '^([1-9]|1[0-6])$'
            THEN (plan->>'batch_size')::int END AS batch_size,
        CASE WHEN jsonb_typeof(plan->'max_checkpoint_bytes')='number'
            AND plan->>'max_checkpoint_bytes' ~ '^[0-9]{1,8}$'
            THEN (plan->>'max_checkpoint_bytes')::int END AS byte_limit,
        CASE WHEN plan->>'schema_version' IN ('waydex-walk-execution/1','waydex-walk-execution/2')
            AND plan->>'checkpoint_format' IS NULL THEN 'waydex-walk-checkpoint/1'
            WHEN plan->>'schema_version'='waydex-walk-execution/3'
            AND plan->>'checkpoint_format'='artifact_refs_v2' THEN 'waydex-walk-checkpoint/2'
        END AS checkpoint_schema
    FROM admitted a
), pairs AS MATERIALIZED (
    SELECT b.job_id,p.item->>'pair_id' AS pair_id,p.ordinality,
        ((p.ordinality-1)/b.batch_size)*b.batch_size AS batch_offset,
        jsonb_typeof(p.item->'pair_id')='string'
            AND p.item->>'pair_id' ~ '^[a-f0-9]{64}$' AS valid_id
    FROM bounded b CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN b.pair_count BETWEEN 1 AND 4096 AND b.batch_size IS NOT NULL
            AND b.byte_limit BETWEEN 1 AND 16777216 AND b.checkpoint_schema IS NOT NULL
            THEN b.plan->'pairs' ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS p(item,ordinality)
), pair_summary AS MATERIALIZED (
    SELECT p.job_id,count(*) AS pair_count,count(DISTINCT p.pair_id) AS distinct_count,
        bool_and(COALESCE(p.valid_id,false)) AS valid_ids,
        array_agg(p.pair_id ORDER BY p.ordinality)=array_agg(p.pair_id ORDER BY p.pair_id COLLATE "C") AS sorted_ids
    FROM pairs p GROUP BY p.job_id
), valid_plan AS MATERIALIZED (
    SELECT b.* FROM bounded b JOIN pair_summary p ON p.job_id=b.job_id
    WHERE p.pair_count=b.pair_count AND p.distinct_count=b.pair_count
        AND p.valid_ids AND p.sorted_ids
), planned AS MATERIALIZED (
    SELECT v.job_id,encode(sha256(convert_to(
        '{"execution":"'||v.execution_identity||'","offset":'||p.batch_offset::text||
        ',"pairs":['||string_agg('"'||p.pair_id||'"',',' ORDER BY p.ordinality)||']}',
        'UTF8')),'hex') AS batch_id,
        array_agg(p.pair_id ORDER BY p.ordinality) AS pair_ids
    FROM valid_plan v JOIN pairs p ON p.job_id=v.job_id
    GROUP BY v.job_id,v.execution_identity,p.batch_offset
), saved_meta AS MATERIALIZED (
    SELECT c.access_scope,c.execution_identity,c.batch_id,c.payload_bytes
    FROM valid_plan v JOIN od_checkpoints c
        ON c.access_scope=$2 AND c.execution_identity=v.execution_identity
    WHERE v.finished_at IS NULL OR c.created_at<=v.finished_at
    ORDER BY c.batch_id LIMIT 4097
), safe_inventory AS MATERIALIZED (
    SELECT v.job_id FROM valid_plan v LEFT JOIN saved_meta s ON true
    GROUP BY v.job_id,v.byte_limit
    HAVING count(s.batch_id)<=4096 AND COALESCE(sum(s.payload_bytes),0)<=v.byte_limit
), checked AS MATERIALIZED (
    SELECT s.batch_id,
        p.batch_id IS NOT NULL AND source_attempt.job_id IS NOT NULL
        AND jsonb_typeof(c.payload)='object'
        AND c.payload->>'batch_id'=c.batch_id
        AND c.payload->>'execution_identity'=c.execution_identity
        AND c.payload->>'schema_version'=v.checkpoint_schema
        AND c.content_hash ~ '^[a-f0-9]{64}$'
        AND p.pair_ids=ARRAY(
            SELECT x.item->>'pair_id' FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(c.payload->'observations')='array'
                    THEN CASE WHEN jsonb_array_length(c.payload->'observations') BETWEEN 1 AND 16
                        THEN c.payload->'observations' ELSE '[]'::jsonb END
                    ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS x(item,ordinality) ORDER BY x.ordinality
        )
        AND NOT EXISTS (
            SELECT 1 FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(c.payload->'observations')='array'
                    THEN CASE WHEN jsonb_array_length(c.payload->'observations') BETWEEN 1 AND 16
                        THEN c.payload->'observations' ELSE '[]'::jsonb END
                    ELSE '[]'::jsonb END
            ) AS x(item) WHERE jsonb_typeof(x.item->'pair_id') IS DISTINCT FROM 'string'
        ) AS valid
    FROM safe_inventory safe JOIN valid_plan v ON v.job_id=safe.job_id
    JOIN saved_meta s ON true
    JOIN od_checkpoints c ON c.access_scope=s.access_scope
        AND c.execution_identity=s.execution_identity AND c.batch_id=s.batch_id
    LEFT JOIN od_attempts source_attempt ON source_attempt.job_id=c.source_job_id
        AND source_attempt.claim_token=c.source_claim_token
        AND source_attempt.access_scope=c.access_scope
        AND source_attempt.execution_identity=c.execution_identity
    LEFT JOIN planned p ON p.job_id=v.job_id AND p.batch_id=s.batch_id
), progress AS (
    SELECT v.job_id,(SELECT count(*) FROM planned)::int AS total_batches,
        (SELECT count(*) FROM checked WHERE valid)::int AS completed_batches,
        NOT EXISTS (SELECT 1 FROM checked WHERE valid IS DISTINCT FROM true) AS valid
    FROM valid_plan v JOIN safe_inventory safe ON safe.job_id=v.job_id
)
SELECT j.job_id,j.status,j.trace_id,j.bundle_id,j.input_hash,j.code_version,
    j.created_at,j.started_at,j.finished_at,j.run_id,j.error,
    p.total_batches,p.completed_batches,p.valid AS valid_progress
FROM owned j LEFT JOIN progress p ON p.job_id=j.job_id
"""


async def job_with_progress(
    repository: "JobRepository", job_id: str, scope: str
) -> tuple[Job, BatchProgress | None]:
    row = await repository.pool.fetchrow(JOB_PROGRESS_SQL, job_id, scope)
    if row is None:
        raise DomainError(404, "unknown_id", "Job not found")
    job = job_from_row(row)
    progress = None
    if row["valid_progress"]:
        completed, total = row["completed_batches"], row["total_batches"]
        phase: Literal["routing", "finalizing", "published", "stopped"] | None
        if job.status == "running":
            phase = "routing" if completed < total else "finalizing"
        elif job.status == "succeeded" and completed == total:
            phase = "published"
        elif job.status in ("failed", "cancelled"):
            phase = "stopped"
        else:
            phase = None
        if phase is not None:
            progress = BatchProgress(completed_batches=completed, total_batches=total, phase=phase)
    return job, progress
