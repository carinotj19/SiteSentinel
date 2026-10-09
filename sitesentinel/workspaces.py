"""Workspace authorization helpers shared by API endpoints."""

from flask_jwt_extended import get_jwt_identity

from sitesentinel.extensions import db
from sitesentinel.models import Membership, Workspace

MONITOR_WRITERS = frozenset({'owner', 'admin', 'member'})
ADMIN_ROLES = frozenset({'owner', 'admin'})


def membership_for(workspace_id: str, user_id: str | None = None) -> Membership | None:
    user_id = user_id or get_jwt_identity()
    return db.session.scalar(
        db.select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.user_id == user_id
        )
    )


def personal_workspace_id(user_id: str | None = None) -> str | None:
    user_id = user_id or get_jwt_identity()
    workspace = db.session.scalar(
        db.select(Workspace).where(Workspace.personal_owner_user_id == user_id)
    )
    return workspace.id if workspace else None
