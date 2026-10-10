"""Tenant-scoped site audit execution and persisted reports."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required

from sitesentinel.api.common import error_response
from sitesentinel.auditing.engine import DEFAULT_LINK_LIMIT, MAX_LINK_LIMIT, audit_site
from sitesentinel.extensions import db
from sitesentinel.models import Monitor, SiteAudit
from sitesentinel.workspaces import MONITOR_WRITERS, membership_for, personal_workspace_id

bp = Blueprint("audits", __name__, url_prefix="/api/v1/monitors/<monitor_id>/audits")


def _authorized(monitor_id: str, workspace_id: str | None, *, write: bool):
    workspace_id = workspace_id or personal_workspace_id()
    member = membership_for(workspace_id) if workspace_id else None
    if member is None:
        return None, error_response("not_found", "Workspace not found.", 404)
    monitor = db.session.scalar(
        db.select(Monitor).where(
            Monitor.id == monitor_id, Monitor.workspace_id == workspace_id
        )
    )
    if monitor is None:
        return None, error_response("not_found", "Monitor not found.", 404)
    if write and member.role not in MONITOR_WRITERS:
        return None, error_response("forbidden", "Read-only workspace role.", 403)
    return monitor, None


def _parse_limit():
    raw = request.args.get("limit", "25")
    if not raw.isascii() or not raw.isdecimal() or not 1 <= len(raw) <= 3:
        return None
    value = int(raw)
    return value if 1 <= value <= 100 else None


@bp.post("")
@jwt_required()
def create_audit(monitor_id: str, workspace_id: str | None = None):
    monitor, error = _authorized(monitor_id, workspace_id, write=True)
    if error is not None:
        return error
    if not monitor.is_enabled:
        return error_response("monitor_disabled", "Enable this monitor before auditing.", 409)
    if request.data:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return error_response("invalid_fields", "Expected a JSON object.", 400)
    else:
        payload = {}
    if set(payload) - {"link_limit"}:
        return error_response("invalid_fields", "Only link_limit is supported.", 400)
    link_limit = payload.get("link_limit", DEFAULT_LINK_LIMIT)
    if isinstance(link_limit, bool) or not isinstance(link_limit, int) or not 0 <= link_limit <= MAX_LINK_LIMIT:
        return error_response("invalid_fields", "link_limit must be an integer between 0 and 10.", 400)

    # On-demand, bounded scanning. Background audit queues are future work.
    result = audit_site(monitor.url, link_limit=link_limit)
    report = SiteAudit(
        monitor_id=monitor.id, requested_url=monitor.url, final_url=result["final_url"],
        status=result["status"], status_code=result["status_code"], link_limit=link_limit,
        findings=result["findings"], error=result["error"],
    )
    db.session.add(report)
    db.session.commit()
    return jsonify({"audit": report.public_dict(include_findings=True)}), 201


@bp.get("")
@jwt_required()
def list_audits(monitor_id: str, workspace_id: str | None = None):
    monitor, error = _authorized(monitor_id, workspace_id, write=False)
    if error is not None:
        return error
    limit = _parse_limit()
    if limit is None:
        return error_response("invalid_pagination", "Use limit 1–100.", 400)
    reports = db.session.scalars(
        db.select(SiteAudit).where(SiteAudit.monitor_id == monitor.id)
        .order_by(SiteAudit.audited_at.desc(), SiteAudit.id.desc()).limit(limit)
    ).all()
    return jsonify({"audits": [row.public_dict() for row in reports]})


@bp.get("/<audit_id>")
@jwt_required()
def get_audit(monitor_id: str, audit_id: str, workspace_id: str | None = None):
    monitor, error = _authorized(monitor_id, workspace_id, write=False)
    if error is not None:
        return error
    report = db.session.scalar(
        db.select(SiteAudit).where(SiteAudit.monitor_id == monitor.id, SiteAudit.id == audit_id)
    )
    if report is None:
        return error_response("not_found", "Audit not found.", 404)
    return jsonify({"audit": report.public_dict(include_findings=True)})
