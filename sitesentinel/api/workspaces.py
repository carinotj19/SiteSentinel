"""Workspaces, tenant-scoped memberships, and single-use team invitations."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from sitesentinel.api.common import error_response, json_object
from sitesentinel.extensions import db
from sitesentinel.models import Invitation, Membership, User, Workspace
from sitesentinel.workspaces import ADMIN_ROLES, membership_for

bp = Blueprint("workspaces", __name__, url_prefix="/api/v1/workspaces")
VALID_ROLES = frozenset({"owner", "admin", "member", "viewer"})
INVITABLE_ROLES = frozenset({"admin", "member", "viewer"})


def _owned(workspace_id, required_role=None):
    member = membership_for(workspace_id)
    if member is None:
        return None, None, error_response("not_found", "Workspace not found.", 404)
    ws = db.session.get(Workspace, workspace_id)
    if required_role and member.role not in required_role:
        return ws, member, error_response("forbidden", "Insufficient workspace permissions.", 403)
    return ws, member, None


def _name(data):
    value = data.get("name")
    return value.strip() if isinstance(value, str) and 2 <= len(value.strip()) <= 100 else None


def _email(value):
    if not isinstance(value, str):
        return None
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


@bp.get("")
@jwt_required()
def list_workspaces():
    memberships = db.session.scalars(
        db.select(Membership).where(Membership.user_id == get_jwt_identity())
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return jsonify({"workspaces": [db.session.get(Workspace, m.workspace_id).public_dict(m.role)
                                   for m in memberships]})


@bp.post("")
@jwt_required()
def create_workspace():
    data = json_object()
    if data is None or set(data) != {"name"} or not _name(data):
        return error_response("invalid_fields", "Provide a workspace name (2–100 characters).", 400)
    ws = Workspace(name=_name(data), created_by_user_id=get_jwt_identity())
    db.session.add(ws)
    db.session.flush()
    db.session.add(Membership(workspace_id=ws.id, user_id=get_jwt_identity(), role="owner"))
    db.session.commit()
    return jsonify({"workspace": ws.public_dict("owner")}), 201


@bp.get("/<workspace_id>")
@jwt_required()
def get_workspace(workspace_id):
    ws, member, error = _owned(workspace_id)
    if error is not None:
        return error
    return jsonify({"workspace": ws.public_dict(member.role)})


@bp.patch("/<workspace_id>")
@jwt_required()
def update_workspace(workspace_id):
    ws, member, error = _owned(workspace_id, {"owner", "admin"})
    if error is not None:
        return error
    data = json_object()
    if data is None or set(data) != {"name"} or not _name(data):
        return error_response("invalid_fields", "Provide a workspace name (2–100 characters).", 400)
    ws.name = _name(data)
    db.session.commit()
    return jsonify({"workspace": ws.public_dict(member.role)})


@bp.get("/<workspace_id>/members")
@jwt_required()
def list_members(workspace_id):
    _, _, error = _owned(workspace_id)
    if error is not None:
        return error
    members = db.session.scalars(
        db.select(Membership).where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at, Membership.id)
    ).all()
    return jsonify({"members": [m.public_dict() for m in members]})


@bp.patch("/<workspace_id>/members/<user_id>")
@jwt_required()
def change_role(workspace_id, user_id):
    _, _, error = _owned(workspace_id, {"owner"})
    if error is not None:
        return error
    member = membership_for(workspace_id, user_id)
    if member is None:
        return error_response("not_found", "Member not found.", 404)
    data = json_object()
    if data is None or set(data) != {"role"} or not isinstance(data.get("role"), str) or data["role"] not in INVITABLE_ROLES:
        return error_response("invalid_role", "Role must be admin, member, or viewer.", 400)
    if member.role == "owner":
        return error_response("owner_protected", "Ownership transfer is not implemented.", 409)
    member.role = data["role"]
    db.session.commit()
    return jsonify({"member": member.public_dict()})


@bp.delete("/<workspace_id>/members/<user_id>")
@jwt_required()
def remove_member(workspace_id, user_id):
    _, _, error = _owned(workspace_id, {"owner"})
    if error is not None:
        return error
    member = membership_for(workspace_id, user_id)
    if member is None:
        return error_response("not_found", "Member not found.", 404)
    if member.role == "owner":
        return error_response("owner_protected", "The owner cannot be removed.", 409)
    db.session.delete(member)
    db.session.commit()
    return "", 204


@bp.post("/<workspace_id>/invitations")
@jwt_required()
def create_invitation(workspace_id):
    _, member, error = _owned(workspace_id, ADMIN_ROLES)
    if error is not None:
        return error
    data = json_object()
    if data is None or set(data) != {"email", "role"}:
        return error_response("invalid_fields", "Provide email and role.", 400)
    email, role = _email(data.get("email")), data.get("role")
    allowed = INVITABLE_ROLES if member.role == "owner" else {"member", "viewer"}
    if not email or not isinstance(role, str) or role not in allowed:
        return error_response("invalid_invitation", "Invalid email or role for your permissions.", 400)
    existing = db.session.scalar(
        db.select(Membership).join(User, Membership.user_id == User.id).where(
            Membership.workspace_id == workspace_id, User.email == email
        )
    )
    if existing:
        return error_response("already_member", "User is already a member.", 409)
    # Raw token is shown once and NOT sent by email. Recipient must register and accept.
    raw = secrets.token_urlsafe(32)
    invite = Invitation(
        workspace_id=workspace_id, email=email, role=role,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        invited_by_user_id=get_jwt_identity(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.session.add(invite)
    db.session.commit()
    return jsonify({"invitation": invite.public_dict(), "token": raw}), 201


@bp.get("/<workspace_id>/invitations")
@jwt_required()
def list_invitations(workspace_id):
    _, _, error = _owned(workspace_id, ADMIN_ROLES)
    if error is not None:
        return error
    invitations = db.session.scalars(
        db.select(Invitation).where(Invitation.workspace_id == workspace_id)
        .order_by(Invitation.created_at.desc()).limit(100)
    ).all()
    return jsonify({"invitations": [item.public_dict() for item in invitations]})


@bp.delete("/<workspace_id>/invitations/<invitation_id>")
@jwt_required()
def revoke_invitation(workspace_id, invitation_id):
    _, _, error = _owned(workspace_id, ADMIN_ROLES)
    if error is not None:
        return error
    item = db.session.scalar(db.select(Invitation).where(
        Invitation.workspace_id == workspace_id, Invitation.id == invitation_id
    ))
    if item is None:
        return error_response("not_found", "Invitation not found.", 404)
    if item.accepted_at is not None:
        return error_response("already_accepted", "Invitation already used.", 409)
    item.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return "", 204


@bp.post("/invitations/accept")
@jwt_required()
def accept_invitation():
    data = json_object()
    if data is None or set(data) != {"token"} or not isinstance(data["token"], str) or len(data["token"]) > 256:
        return error_response("invalid_invitation", "Valid invitation token required.", 400)
    digest = hashlib.sha256(data["token"].encode()).hexdigest()
    now = datetime.now(timezone.utc)
    invitation = db.session.scalar(db.select(Invitation).where(Invitation.token_hash == digest))
    if invitation is None or invitation.accepted_at or invitation.revoked_at:
        return error_response("invalid_invitation", "Invitation invalid or no longer available.", 404)
    expiry = invitation.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now:
        return error_response("expired_invitation", "Invitation has expired.", 410)
    user = db.session.get(User, get_jwt_identity())
    if invitation.email != user.email:
        return error_response("wrong_account", "Invitation does not match this account's email.", 403)
    membership = Membership(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role)
    db.session.add(membership)
    invitation.accepted_at = now
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error_response("already_member", "User already belongs to this workspace.", 409)
    ws = db.session.get(Workspace, invitation.workspace_id)
    return jsonify({"workspace": ws.public_dict(membership.role)}), 200
