"""Store bounded website audit reports.

Revision ID: 0005_site_audits
Revises: 0004_workspaces_rbac
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_site_audits"
down_revision = "0004_workspaces_rbac"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "site_audits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), nullable=False),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_url", sa.String(2048), nullable=False),
        sa.Column("final_url", sa.String(2048)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_code", sa.Integer()),
        sa.Column("link_limit", sa.Integer(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(400)),
        sa.ForeignKeyConstraint(["monitor_id"], ["monitors.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_site_audits_monitor_audited", "site_audits", ["monitor_id", "audited_at"])


def downgrade():
    op.drop_index("ix_site_audits_monitor_audited", table_name="site_audits")
    op.drop_table("site_audits")
