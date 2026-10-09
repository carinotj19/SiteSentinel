"""Schedule checks and persist outage incidents and alert events.

Revision ID: 0003_background_jobs
Revises: 0002_monitors_and_checks
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_background_jobs"
down_revision = "0002_monitors_and_checks"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("monitors") as batch:
        batch.add_column(sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="300"))
        batch.add_column(sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("failure_streak", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("lease_token", sa.String(36), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    # Existing enabled monitors become immediately due, preserving their existing check history.
    op.execute("UPDATE monitors SET next_check_at = CURRENT_TIMESTAMP WHERE is_enabled = true")
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("reason", sa.String(500)),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_incidents_monitor_resolved", "incidents", ["monitor_id", "resolved_at"])
    op.create_table(
        "alert_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(400)),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_alert_events_monitor_created", "alert_events", ["monitor_id", "created_at"])


def downgrade():
    op.drop_index("ix_alert_events_monitor_created", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_incidents_monitor_resolved", table_name="incidents")
    op.drop_table("incidents")
    with op.batch_alter_table("monitors") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_token")
        batch.drop_column("failure_streak")
        batch.drop_column("next_check_at")
        batch.drop_column("interval_seconds")
