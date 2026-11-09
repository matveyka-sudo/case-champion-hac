"""Add scoped v2 artifacts, immutable proposals and fenced single-attempt leases.

Revision ID: 0002_v2_jobs
Revises: 0001_jobs_runs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_v2_jobs"
down_revision = "0001_jobs_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    for table in ("jobs", "runs"):
        op.add_column(
            table,
            sa.Column("schema_version", sa.Text(), nullable=False, server_default="1"),
            schema=schema,
        )
        op.add_column(table, sa.Column("access_scope", sa.Text(), nullable=True), schema=schema)
        op.create_check_constraint(
            f"ck_{table}_scope",
            table,
            "(schema_version='1' AND access_scope IS NULL) OR "
            "(schema_version='2' AND access_scope IS NOT NULL AND length(access_scope)>0)",
            schema=schema,
        )
    op.add_column("jobs", sa.Column("cache_valid_until", sa.DateTime(timezone=True)), schema=schema)
    op.add_column(
        "jobs",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        schema=schema,
    )
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)), schema=schema)
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True)), schema=schema)
    # Existing running jobs remain fenced; restart recovery fails them rather than rerunning.
    op.execute(
        sa.text(
            f'UPDATE "{schema}".jobs SET attempts=1,heartbeat_at=started_at,lease_expires_at=now() '
            "WHERE status='running'"
        )
    )
    op.create_check_constraint(
        "ck_jobs_attempts", "jobs", "attempts BETWEEN 0 AND 1", schema=schema
    )
    op.create_check_constraint(
        "ck_jobs_running_lease",
        "jobs",
        "status<>'running' OR "
        "(attempts=1 AND heartbeat_at IS NOT NULL AND lease_expires_at IS NOT NULL)",
        schema=schema,
    )
    op.drop_constraint("ck_runs_result_kind", "runs", schema=schema)
    op.create_check_constraint(
        "ck_runs_result_kind",
        "runs",
        "result_kind IN ('profile','comparison','analysis_v2','scenario_v2')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_runs_schema_kind",
        "runs",
        "(schema_version='1' AND result_kind IN ('profile','comparison')) OR "
        "(schema_version='2' AND result_kind IN ('analysis_v2','scenario_v2'))",
        schema=schema,
    )
    op.create_table(
        "scenario_proposals",
        sa.Column("scenario_id", sa.Text(), primary_key=True),
        sa.Column(
            "baseline_analysis_id",
            sa.Text(),
            sa.ForeignKey(f"{schema}.runs.run_id"),
            nullable=False,
        ),
        sa.Column("access_scope", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema=schema,
    )
    op.create_index(
        "ix_proposals_baseline_scope",
        "scenario_proposals",
        ["baseline_analysis_id", "access_scope"],
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    # Never silently delete immutable v2 artifacts to make them fit the old schema.
    remaining = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT (SELECT count(*) FROM \"{schema}\".jobs WHERE schema_version='2') + "
                f"(SELECT count(*) FROM \"{schema}\".runs WHERE schema_version='2')"
            )
        )
        .scalar_one()
    )
    if remaining:
        raise RuntimeError("Cannot downgrade while v2 jobs/artifacts remain")
    op.drop_table("scenario_proposals", schema=schema)
    for name in ("ck_runs_schema_kind", "ck_runs_result_kind"):
        op.drop_constraint(name, "runs", schema=schema)
    op.create_check_constraint(
        "ck_runs_result_kind", "runs", "result_kind IN ('profile','comparison')", schema=schema
    )
    for name in ("ck_jobs_running_lease", "ck_jobs_attempts"):
        op.drop_constraint(name, "jobs", schema=schema)
    for name in ("lease_expires_at", "heartbeat_at", "attempts", "cache_valid_until"):
        op.drop_column("jobs", name, schema=schema)
    for table in ("jobs", "runs"):
        op.drop_constraint(f"ck_{table}_scope", table, schema=schema)
        op.drop_column(table, "access_scope", schema=schema)
        op.drop_column(table, "schema_version", schema=schema)
