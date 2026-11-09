"""Create schema-local immutable results and durable analysis jobs.

Revision ID: 0001_jobs_runs
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001_jobs_runs"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("result_kind", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("result_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("result_kind IN ('profile', 'comparison')", name="ck_runs_result_kind"),
        schema=schema,
    )
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("bundle_id", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("cache_key", sa.Text(), nullable=False),
        sa.Column("code_version", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "run_id",
            sa.Text(),
            sa.ForeignKey(f"{schema}.runs.run_id", name="fk_jobs_run_id"),
            nullable=True,
        ),
        sa.Column("error", JSONB(), nullable=True),
        sa.Column("claim_token", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('analysis', 'scenario')", name="ck_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND finished_at IS NULL "
            "AND run_id IS NULL AND error IS NULL AND claim_token IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND claim_token IS NOT NULL "
            "AND finished_at IS NULL AND run_id IS NULL AND error IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND run_id IS NOT NULL AND claim_token IS NOT NULL AND error IS NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL "
            "AND run_id IS NULL) OR "
            "(status = 'cancelled' AND finished_at IS NOT NULL AND error IS NULL "
            "AND run_id IS NULL)",
            name="ck_jobs_state_coherence",
        ),
        sa.UniqueConstraint("kind", "idempotency_key", name="uq_jobs_kind_idempotency_key"),
        schema=schema,
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"], schema=schema)
    op.create_index(
        "ix_jobs_succeeded_cache_key",
        "jobs",
        ["cache_key"],
        schema=schema,
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.drop_table("jobs", schema=schema)
    op.drop_table("runs", schema=schema)
