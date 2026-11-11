"""Private immutable OD manifests/checkpoints; successful runs remain separate.

Revision ID: 0005_od_checkpoints
Revises: 0004_alice_budget
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_od_checkpoints"
down_revision = "0004_alice_budget"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "od_executions",
        sa.Column("access_scope", sa.Text(), primary_key=True),
        sa.Column("execution_identity", sa.Text(), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("attempts>=0 AND reserved_calls>=0", name="ck_od_execution_counters"),
        schema=schema,
    )
    op.create_table(
        "od_attempts",
        sa.Column("job_id", sa.Text(), primary_key=True),
        sa.Column("claim_token", sa.Text(), nullable=False),
        sa.Column("access_scope", sa.Text(), nullable=False),
        sa.Column("execution_identity", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], [f"{schema}.jobs.job_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["access_scope", "execution_identity"],
            [f"{schema}.od_executions.access_scope", f"{schema}.od_executions.execution_identity"],
            ondelete="RESTRICT",
        ),
        schema=schema,
    )
    op.create_table(
        "od_checkpoints",
        sa.Column("access_scope", sa.Text(), primary_key=True),
        sa.Column("execution_identity", sa.Text(), primary_key=True),
        sa.Column("batch_id", sa.Text(), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("payload_bytes", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Text(), nullable=False),
        sa.Column("source_claim_token", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["access_scope", "execution_identity"],
            [f"{schema}.od_executions.access_scope", f"{schema}.od_executions.execution_identity"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["source_job_id"], [f"{schema}.jobs.job_id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "payload_bytes>0 AND payload_bytes<=8388608", name="ck_od_checkpoint_bytes"
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.drop_table("od_checkpoints", schema=schema)
    op.drop_table("od_attempts", schema=schema)
    op.drop_table("od_executions", schema=schema)
