"""Durable serial category execution, referencing ordinary fenced jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = "0008_analysis_groups"
down_revision = "0007_private_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "analysis_groups",
        sa.Column("group_id", sa.Text(), primary_key=True),
        sa.Column("access_scope", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False, unique=True),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("plan", JSONB(), nullable=False),
        sa.Column("plan_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("error", JSONB(), nullable=True),
        sa.Column("retry_keys", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_group_status",
        ),
        schema=schema,
    )
    op.create_index("ix_group_dispatch", "analysis_groups", ["status", "created_at"], schema=schema)
    op.create_table(
        "analysis_group_items",
        sa.Column(
            "group_id",
            sa.Text(),
            sa.ForeignKey(f"{schema}.analysis_groups.group_id"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", JSONB(), nullable=True),
        sa.Column(
            "job_id", sa.Text(), sa.ForeignKey(f"{schema}.jobs.job_id"), nullable=True, unique=True
        ),
        sa.Column("previous_job_ids", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.CheckConstraint("ordinal >= 0 AND ordinal < 9", name="ck_group_ordinal"),
        sa.CheckConstraint("attempt >= 1 AND attempt <= 3", name="ck_group_attempt"),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    if (
        op.get_bind()
        .execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM "{schema}".analysis_groups)'))
        .scalar()
    ):
        raise RuntimeError("Analysis group history must not be discarded")
    op.drop_table("analysis_group_items", schema=schema)
    op.drop_index("ix_group_dispatch", table_name="analysis_groups", schema=schema)
    op.drop_table("analysis_groups", schema=schema)
