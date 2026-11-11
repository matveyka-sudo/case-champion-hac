"""Immutable reviewed evidence chunks and Russian full-text retrieval.

Revision ID: 0003_evidence_search
Revises: 0002_v2_jobs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

revision = "0003_evidence_search"
down_revision = "0002_v2_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "evidence_chunks",
        sa.Column("snapshot_id", sa.Text(), primary_key=True),
        sa.Column("chunk_id", sa.Text(), primary_key=True),
        sa.Column("snapshot_hash", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("territory_ids", ARRAY(sa.Text()), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("record_hash", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            TSVECTOR(),
            sa.Computed(
                "to_tsvector('russian'::regconfig, title || ' ' || section || ' ' || body)",
                persisted=True,
            ),
        ),
        sa.CheckConstraint("valid_from<=valid_to", name="ck_evidence_period"),
        schema=schema,
    )
    op.create_index(
        "ix_evidence_text",
        "evidence_chunks",
        ["search_vector"],
        postgresql_using="gin",
        schema=schema,
    )
    op.create_index(
        "ix_evidence_scope",
        "evidence_chunks",
        ["snapshot_id", "snapshot_hash", "source_id"],
        schema=schema,
    )
    op.create_index(
        "ix_evidence_territories",
        "evidence_chunks",
        ["territory_ids"],
        postgresql_using="gin",
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    if (
        op.get_bind()
        .execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM "{schema}".evidence_chunks)'))
        .scalar_one()
    ):
        raise RuntimeError("Cannot downgrade while accepted evidence chunks remain")
    op.drop_table("evidence_chunks", schema=schema)
