"""Owner-scoped monitor CRUD and synchronous development-time checks."""

from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, g, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from sitesentinel.api.common import error_response, json_object
from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, CheckResult, Incident, Monitor
from sitesentinel.monitoring.scanner import UnsafeTarget, normalize_url, scan_url
from sitesentinel.monitoring.service import record_scan
from sitesentinel.workspaces import MONITOR_WRITERS, membership_for, personal_workspace_id

bp = Blueprint("monitors", __name__, url_prefix="/api/v1/monitors")


def workspace_guard(handler):
    """Authorize the requested workspace before any monitor query/mutation."""
    @wraps(handler)
    def wrapped(*args, workspace_id=None, **kwargs):
        workspace_id = workspace_id or personal_workspace_id()
        member = membership_for(workspace_id) if workspace_id else None
        if member is None:
            return error_response("not_found", "Workspace not found.", 404)
        if request.method != "GET" and member.role not in MONITOR_WRITERS:
            return error_response("forbidden", "Read-only workspace role.", 403)
        g.workspace_id = workspace_id
        return handler(*args, **kwargs)
    return wrapped


def _owned_monitor(monitor_id: str) -> Monitor | None:
    return db.session.scalar(
        db.select(Monitor).where(Monitor.id == monitor_id, Monitor.workspace_id == g.workspace_id)
    )


def _bad_fields(payload: dict, allowed: set[str]) -> bool:
    return not payload or bool(set(payload) - allowed)


def _apply_fields(monitor: Monitor, payload: dict):
    now = datetime.now(timezone.utc)
    if "interval_seconds" in payload:
        interval = payload["interval_seconds"]
        if isinstance(interval, bool) or not isinstance(interval, int) or not 60 <= interval <= 86400:
            raise ValueError("interval_seconds must be an integer between 60 and 86400.")
        if interval != monitor.interval_seconds:
            monitor.interval_seconds = interval
            monitor.next_check_at = now if monitor.is_enabled else None
    if "name" in payload:
        name = payload["name"]
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("Name must be between 1 and 100 characters.")
        monitor.name = name.strip()
    if "url" in payload:
        next_url = normalize_url(payload["url"])
        if monitor.url != next_url:
            monitor.url = next_url
            monitor.last_status = None
            monitor.last_checked_at = None
            monitor.failure_streak = 0
            monitor.next_check_at = now if monitor.is_enabled else None
            monitor.lease_token = None
            monitor.lease_expires_at = None
    if "is_enabled" in payload:
        if not isinstance(payload["is_enabled"], bool):
            raise ValueError("is_enabled must be a boolean.")
        if monitor.is_enabled != payload["is_enabled"]:
            monitor.is_enabled = payload["is_enabled"]
            monitor.next_check_at = now if monitor.is_enabled else None
            monitor.lease_token = None
            monitor.lease_expires_at = None
            if not monitor.is_enabled:
                monitor.failure_streak = 0


def _save():
    try:
        db.session.commit()
        return None
    except IntegrityError:
        db.session.rollback()
        return error_response("duplicate_monitor", "This URL is already monitored by your account.", 409)


def _parse_limit(default: int, maximum: int) -> int | None:
    raw = request.args.get("limit", str(default))
    try:
        value = int(raw)
        return value if 1 <= value <= maximum else None
    except ValueError:
        return None


@bp.get("")
@jwt_required()
@workspace_guard
def list_monitors():
    limit = _parse_limit(50, 200)
    raw_offset = request.args.get("offset", "0")
    if limit is None or len(raw_offset) > 8 or not raw_offset.isascii() or not raw_offset.isdecimal():
        return error_response("invalid_pagination", "Use limit 1–200 and a nonnegative offset.", 400)
    offset = int(raw_offset)
    total = db.session.scalar(db.select(db.func.count()).select_from(Monitor).where(Monitor.workspace_id == g.workspace_id))
    rows = db.session.scalars(
        db.select(Monitor).where(Monitor.workspace_id == g.workspace_id)
        .order_by(Monitor.created_at.desc(), Monitor.id.desc()).offset(offset).limit(limit)
    ).all()
    return jsonify({"monitors": [row.public_dict() for row in rows], "total": total})


@bp.post("")
@jwt_required()
@workspace_guard
def create_monitor():
    data = json_object()
    if data is None or _bad_fields(data, {"name", "url", "is_enabled", "interval_seconds"}):
        return error_response("invalid_fields", "JSON may contain only name, url, is_enabled, interval_seconds.", 400)
    if "name" not in data or "url" not in data:
        return error_response("missing_fields", "name and url are required.", 400)
    monitor = Monitor(
        user_id=get_jwt_identity(), workspace_id=g.workspace_id, is_enabled=True, interval_seconds=300,
        failure_streak=0, next_check_at=datetime.now(timezone.utc),
    )
    try:
        _apply_fields(monitor, data)
    except (ValueError, UnsafeTarget) as exc:
        return error_response("invalid_monitor", str(exc), 400)
    # A disabled monitor must never retain a due timestamp at creation.
    if not monitor.is_enabled:
        monitor.next_check_at = None
    db.session.add(monitor)
    error = _save()
    if error is not None:
        return error
    return jsonify({"monitor": monitor.public_dict()}), 201


@bp.get("/<monitor_id>")
@jwt_required()
@workspace_guard
def get_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    return jsonify({"monitor": monitor.public_dict()})


@bp.patch("/<monitor_id>")
@jwt_required()
@workspace_guard
def update_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    data = json_object()
    if data is None or _bad_fields(data, {"name", "url", "is_enabled", "interval_seconds"}):
        return error_response("invalid_fields", "JSON may contain only name, url, is_enabled, interval_seconds.", 400)
    prior_url, prior_enabled = monitor.url, monitor.is_enabled
    try:
        _apply_fields(monitor, data)
    except (ValueError, UnsafeTarget) as exc:
        return error_response("invalid_monitor", str(exc), 400)
    if monitor.url != prior_url or (prior_enabled and not monitor.is_enabled):
        open_incidents = db.session.scalars(
            db.select(Incident).where(Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None))
        ).all()
        for incident in open_incidents:
            incident.resolved_at = datetime.now(timezone.utc)
    error = _save()
    if error is not None:
        return error
    return jsonify({"monitor": monitor.public_dict()})


@bp.delete("/<monitor_id>")
@jwt_required()
@workspace_guard
def delete_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    db.session.delete(monitor)
    db.session.commit()
    return "", 204


@bp.post("/<monitor_id>/check")
@jwt_required()
@workspace_guard
def run_check(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    if not monitor.is_enabled:
        return error_response("monitor_disabled", "Enable this monitor before checking.", 409)
    # Intentionally synchronous for local use; workers will replace it in the next feature.
    result = scan_url(monitor.url)
    check = record_scan(monitor, result)
    db.session.commit()
    return jsonify({"check": check.public_dict()}), 201


@bp.get("/<monitor_id>/checks")
@jwt_required()
@workspace_guard
def list_checks(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    limit = _parse_limit(25, 100)
    if limit is None:
        return error_response("invalid_pagination", "Use limit 1–100.", 400)
    rows = db.session.scalars(
        db.select(CheckResult).where(CheckResult.monitor_id == monitor.id)
        .order_by(CheckResult.checked_at.desc(), CheckResult.id.desc()).limit(limit)
    ).all()
    return jsonify({"checks": [row.public_dict() for row in rows]})


@bp.get("/<monitor_id>/incidents")
@jwt_required()
@workspace_guard
def list_incidents(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    limit = _parse_limit(25, 100)
    if limit is None:
        return error_response("invalid_pagination", "Use limit 1–100.", 400)
    rows = db.session.scalars(
        db.select(Incident).where(Incident.monitor_id == monitor.id)
        .order_by(Incident.opened_at.desc(), Incident.id.desc()).limit(limit)
    ).all()
    return jsonify({"incidents": [row.public_dict() for row in rows]})


@bp.get("/<monitor_id>/alerts")
@jwt_required()
@workspace_guard
def list_alerts(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    limit = _parse_limit(25, 100)
    if limit is None:
        return error_response("invalid_pagination", "Use limit 1–100.", 400)
    rows = db.session.scalars(
        db.select(AlertEvent).where(AlertEvent.monitor_id == monitor.id)
        .order_by(AlertEvent.created_at.desc(), AlertEvent.id.desc()).limit(limit)
    ).all()
    return jsonify({"alerts": [row.public_dict() for row in rows]})
