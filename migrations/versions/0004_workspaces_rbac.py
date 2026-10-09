"""Introduce workspaces, roles, and invitations without losing existing monitors.

Revision ID: 0004_workspaces_rbac
Revises: 0003_background_jobs
"""

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0004_workspaces_rbac"
down_revision = "0003_background_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_by_user_id", sa.String(36), nullable=False),
        sa.Column("personal_owner_user_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["personal_owner_user_id"], ["users.id"]),
        sa.UniqueConstraint("personal_owner_user_id"),
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_membership_workspace_user"),
    )
    op.create_index("ix_workspace_memberships_workspace_id", "workspace_memberships", ["workspace_id"])
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    op.create_table(
        "workspace_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("role", sa.String(12), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("invited_by_user_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"]),
    )
    op.create_index("ix_workspace_invitations_workspace_id", "workspace_invitations", ["workspace_id"])
    op.create_index("ix_workspace_invitations_token_hash", "workspace_invitations", ["token_hash"], unique=True)
    # Add as nullable, backfill, and enforce not-null after existing monitors are assigned.
    with op.batch_alter_table("monitors") as batch:
        batch.add_column(sa.Column("workspace_id", sa.String(36), nullable=True))

    connection = op.get_bind()
    existing_user_ids = connection.execute(sa.text("SELECT id FROM users")).scalars().all()
    now = datetime.now(timezone.utc)
    for user_id in existing_user_ids:
        workspace_id = str(uuid4())
        connection.execute(sa.text(
            "INSERT INTO workspaces (id, name, created_by_user_id, personal_owner_user_id, created_at) "
            "VALUES (:id, :name, :user_id, :user_id, :now)"
        ), {"id": workspace_id, "name": "Personal Workspace", "user_id": user_id, "now": now})
        connection.execute(sa.text(
            "INSERT INTO workspace_memberships (id, workspace_id, user_id, role, created_at) "
            "VALUES (:id, :workspace_id, :user_id, 'owner', :now)"
        ), {"id": str(uuid4()), "workspace_id": workspace_id, "user_id": user_id, "now": now})
        connection.execute(sa.text(
            "UPDATE monitors SET workspace_id = :workspace_id WHERE user_id = :user_id"
        ), {"workspace_id": workspace_id, "user_id": user_id})

    with op.batch_alter_table("monitors") as batch:
        batch.alter_column("workspace_id", existing_type=sa.String(36), nullable=False)
        batch.create_foreign_key("fk_monitors_workspace_id", "workspaces", ["workspace_id"], ["id"], ondelete="CASCADE")
        batch.create_index("ix_monitors_workspace_id", ["workspace_id"])
        batch.drop_constraint("uq_monitors_user_url", type_="unique")
        batch.create_unique_constraint("uq_monitors_workspace_url", ["workspace_id", "url"])


def downgrade():
    # Down migration only supports tenants where no duplicate creator/URL combinations exist.
    with op.batch_alter_table("monitors") as batch:
        batch.drop_constraint("uq_monitors_workspace_url", type_="unique")
        batch.create_unique_constraint("uq_monitors_user_url", ["user_id", "url"])
        batch.drop_index("ix_monitors_workspace_id")
        batch.drop_constraint("fk_monitors_workspace_id", type_="foreignkey")
        batch.drop_column("workspace_id")
    op.drop_index("ix_workspace_invitations_token_hash", table_name="workspace_invitations")
    op.drop_index("ix_workspace_invitations_workspace_id", table_name="workspace_invitations")
    op.drop_table("workspace_invitations")
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_index("ix_workspace_memberships_workspace_id", table_name="workspace_memberships")
    op.drop_table("workspace_memberships")
    op.drop_table("workspaces")
