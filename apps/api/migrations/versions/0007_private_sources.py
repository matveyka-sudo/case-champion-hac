"""Private source grants and separately audited retention state.

Revision ID: 0007_private_sources
Revises: 0006_rasp_quota
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_private_sources"
down_revision = "0006_rasp_quota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "private_source_episodes",
        sa.Column("episode_id", sa.Text(), primary_key=True),
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="active"),
        sa.Column("purge_receipt_hash", sa.Text(), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at>observed_at", name="ck_private_source_interval"),
        sa.CheckConstraint(
            "state IN ('active','purging','purged')", name="ck_private_source_state"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_private_source_snapshot", "private_source_episodes", ["snapshot_id"], schema=schema
    )
    op.create_table(
        "private_source_grants",
        sa.Column("grant_id", sa.Text(), primary_key=True),
        sa.Column(
            "episode_id",
            sa.Text(),
            sa.ForeignKey(f"{schema}.private_source_episodes.episode_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("access_scope", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("valid_until>valid_from", name="ck_private_source_grant_interval"),
        sa.UniqueConstraint("episode_id", "access_scope", name="uq_private_source_grant_scope"),
        schema=schema,
    )
    op.add_column(
        "runs", sa.Column("private_source_bindings", JSONB(), nullable=True), schema=schema
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    initialized = (
        op.get_bind()
        .execute(
            sa.text(
                f'SELECT EXISTS(SELECT 1 FROM "{schema}".private_source_episodes) OR EXISTS(SELECT 1 FROM "{schema}".runs WHERE private_source_bindings IS NOT NULL)'
            )
        )
        .scalar()
    )
    if initialized:
        raise RuntimeError("Private source evidence and retention history must not be discarded")
    op.drop_column("runs", "private_source_bindings", schema=schema)
    op.drop_table("private_source_grants", schema=schema)
    op.drop_index("ix_private_source_snapshot", table_name="private_source_episodes", schema=schema)
    op.drop_table("private_source_episodes", schema=schema)
