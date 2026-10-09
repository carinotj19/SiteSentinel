"""Add user-owned monitors and immutable check history.

Revision ID: 0002_monitors_and_checks
Revises: 0001_users_and_tokens
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_monitors_and_checks"
down_revision = "0001_users_and_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "monitors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_status", sa.String(12)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "url", name="uq_monitors_user_url"),
    )
    op.create_index("ix_monitors_user_id", "monitors", ["user_id"])
    op.create_table(
        "check_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_url", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("response_time_ms", sa.Integer()),
        sa.Column("certificate_expires_at", sa.DateTime(timezone=True)),
        sa.Column("final_url", sa.String(2048)),
        sa.Column("error", sa.String(500)),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_check_results_monitor_checked", "check_results", ["monitor_id", "checked_at"])


def downgrade():
    op.drop_index("ix_check_results_monitor_checked", table_name="check_results")
    op.drop_table("check_results")
    op.drop_index("ix_monitors_user_id", table_name="monitors")
    op.drop_table("monitors")
