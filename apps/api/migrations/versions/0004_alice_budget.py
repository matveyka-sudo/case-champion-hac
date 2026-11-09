"""Durable deployment-wide Alice spending and token reservations.

Revision ID: 0004_alice_budget
Revises: 0003_evidence_search
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_alice_budget"
down_revision = "0003_evidence_search"
branch_labels = None
depends_on = None

AMOUNT = sa.Numeric(24, 9)


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "assistant_budget",
        sa.Column("budget_key", sa.Text(), primary_key=True),
        sa.Column("model_uri", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("tariff_id", sa.Text(), nullable=False),
        sa.Column("limit_amount", AMOUNT, nullable=False),
        sa.Column("charged_amount", AMOUNT, nullable=False, server_default="0"),
        sa.Column("held_amount", AMOUNT, nullable=False, server_default="0"),
        sa.Column("token_limit", sa.BigInteger(), nullable=False),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("held_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("budget_key='deployment'", name="ck_assistant_budget_key"),
        sa.CheckConstraint("limit_amount>0", name="ck_assistant_budget_limit"),
        sa.CheckConstraint(
            "charged_amount>=0 AND held_amount>=0", name="ck_assistant_budget_money"
        ),
        sa.CheckConstraint("token_limit>0", name="ck_assistant_budget_token_limit"),
        sa.CheckConstraint(
            "charged_tokens>=0 AND held_tokens>=0", name="ck_assistant_budget_tokens"
        ),
        schema=schema,
    )
    op.create_table(
        "assistant_reservations",
        sa.Column("reservation_id", sa.Text(), primary_key=True),
        sa.Column("budget_key", sa.Text(), nullable=False),
        sa.Column("model_uri", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("tariff_id", sa.Text(), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_amount", AMOUNT, nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("charged_amount", AMOUNT, nullable=True),
        sa.Column("charged_tokens", sa.BigInteger(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False, server_default="held"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["budget_key"], [f"{schema}.assistant_budget.budget_key"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "max_input_tokens>0 AND max_output_tokens>0", name="ck_assistant_reservation_max"
        ),
        sa.CheckConstraint(
            "reserved_amount>0 AND reserved_tokens>0", name="ck_assistant_reservation_hold"
        ),
        sa.CheckConstraint(
            "state IN ('held','unknown','settled')", name="ck_assistant_reservation_state"
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    if (
        op.get_bind()
        .execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM "{schema}".assistant_budget)'))
        .scalar_one()
    ):
        raise RuntimeError("Cannot downgrade while an assistant budget exists")
    op.drop_table("assistant_reservations", schema=schema)
    op.drop_table("assistant_budget", schema=schema)
