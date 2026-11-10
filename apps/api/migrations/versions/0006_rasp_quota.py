"""Shared explicitly initialized account/day Rasp quota.

Revision ID: 0006_rasp_quota
Revises: 0005_od_checkpoints
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_rasp_quota"
down_revision = "0005_od_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    op.create_table(
        "rasp_quota_accounts",
        sa.Column("account_id", sa.Text(), primary_key=True),
        sa.Column("ledger_scope_id", sa.Text(), nullable=False, unique=True),
        sa.Column("quota_timezone", sa.Text(), nullable=False),
        schema=schema,
    )
    op.create_table(
        "rasp_quota_credentials",
        sa.Column("credential_fingerprint", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], [f"{schema}.rasp_quota_accounts.account_id"], ondelete="RESTRICT"
        ),
        schema=schema,
    )
    op.create_table(
        "rasp_quota_days",
        sa.Column("account_id", sa.Text(), primary_key=True),
        sa.Column("quota_day", sa.Date(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allowance", sa.Integer(), nullable=False),
        sa.Column("already_used", sa.Integer(), nullable=False),
        sa.Column("reserved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("baseline_ref", sa.Text(), nullable=False),
        sa.Column("exclusive_usage_attested", sa.Boolean(), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "initialized_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], [f"{schema}.rasp_quota_accounts.account_id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "allowance BETWEEN 1 AND 500 AND already_used BETWEEN 0 AND 500 AND reserved_count>=0 AND already_used+reserved_count<=500",
            name="ck_rasp_daily_hard_cap",
        ),
        sa.CheckConstraint(
            "window_end>window_start AND exclusive_usage_attested", name="ck_rasp_known_day"
        ),
        schema=schema,
    )
    op.create_table(
        "rasp_quota_attempts",
        sa.Column("reservation_id", sa.Text(), primary_key=True),
        sa.Column("account_id", sa.Text(), nullable=False),
        sa.Column("quota_day", sa.Date(), nullable=False),
        sa.Column("credential_fingerprint", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("day_reserved_total", sa.Integer(), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id", "quota_day"],
            [f"{schema}.rasp_quota_days.account_id", f"{schema}.rasp_quota_days.quota_day"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credential_fingerprint"],
            [f"{schema}.rasp_quota_credentials.credential_fingerprint"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "account_id", "quota_day", "day_reserved_total", name="uq_rasp_daily_attempt_counter"
        ),
        sa.CheckConstraint("day_reserved_total BETWEEN 1 AND 500", name="ck_rasp_attempt_hard_cap"),
        schema=schema,
    )


def downgrade() -> None:
    schema = op.get_context().opts["version_table_schema"]
    initialized = (
        op.get_bind()
        .execute(sa.text(f'SELECT EXISTS(SELECT 1 FROM "{schema}".rasp_quota_accounts)'))
        .scalar()
    )
    if initialized:
        raise RuntimeError("Rasp quota history must not be removed by a migration downgrade")
    for name in (
        "rasp_quota_attempts",
        "rasp_quota_days",
        "rasp_quota_credentials",
        "rasp_quota_accounts",
    ):
        op.drop_table(name, schema=schema)
