"""Owner-scoped monitor CRUD and synchronous development-time checks."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from sitesentinel.api.common import error_response, json_object
from sitesentinel.extensions import db
from sitesentinel.models import CheckResult, Monitor
from sitesentinel.monitoring.scanner import UnsafeTarget, normalize_url, scan_url

bp = Blueprint("monitors", __name__, url_prefix="/api/v1/monitors")


def _owned_monitor(monitor_id: str) -> Monitor | None:
    return db.session.scalar(
        db.select(Monitor).where(Monitor.id == monitor_id, Monitor.user_id == get_jwt_identity())
    )


def _bad_fields(payload: dict, allowed: set[str]) -> bool:
    return not payload or bool(set(payload) - allowed)


def _apply_fields(monitor: Monitor, payload: dict):
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
    if "is_enabled" in payload:
        if not isinstance(payload["is_enabled"], bool):
            raise ValueError("is_enabled must be a boolean.")
        monitor.is_enabled = payload["is_enabled"]


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
def list_monitors():
    limit = _parse_limit(50, 200)
    raw_offset = request.args.get("offset", "0")
    if limit is None or len(raw_offset) > 8 or not raw_offset.isascii() or not raw_offset.isdecimal():
        return error_response("invalid_pagination", "Use limit 1–200 and a nonnegative offset.", 400)
    offset = int(raw_offset)
    owner_id = get_jwt_identity()
    total = db.session.scalar(db.select(db.func.count()).select_from(Monitor).where(Monitor.user_id == owner_id))
    rows = db.session.scalars(
        db.select(Monitor).where(Monitor.user_id == owner_id)
        .order_by(Monitor.created_at.desc(), Monitor.id.desc()).offset(offset).limit(limit)
    ).all()
    return jsonify({"monitors": [row.public_dict() for row in rows], "total": total})


@bp.post("")
@jwt_required()
def create_monitor():
    data = json_object()
    if data is None or _bad_fields(data, {"name", "url", "is_enabled"}):
        return error_response("invalid_fields", "JSON may contain only name, url, is_enabled.", 400)
    if "name" not in data or "url" not in data:
        return error_response("missing_fields", "name and url are required.", 400)
    monitor = Monitor(user_id=get_jwt_identity())
    try:
        _apply_fields(monitor, data)
    except (ValueError, UnsafeTarget) as exc:
        return error_response("invalid_monitor", str(exc), 400)
    db.session.add(monitor)
    error = _save()
    if error is not None:
        return error
    return jsonify({"monitor": monitor.public_dict()}), 201


@bp.get("/<monitor_id>")
@jwt_required()
def get_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    return jsonify({"monitor": monitor.public_dict()})


@bp.patch("/<monitor_id>")
@jwt_required()
def update_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    data = json_object()
    if data is None or _bad_fields(data, {"name", "url", "is_enabled"}):
        return error_response("invalid_fields", "JSON may contain only name, url, is_enabled.", 400)
    try:
        _apply_fields(monitor, data)
    except (ValueError, UnsafeTarget) as exc:
        return error_response("invalid_monitor", str(exc), 400)
    error = _save()
    if error is not None:
        return error
    return jsonify({"monitor": monitor.public_dict()})


@bp.delete("/<monitor_id>")
@jwt_required()
def delete_monitor(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    db.session.delete(monitor)
    db.session.commit()
    return "", 204


@bp.post("/<monitor_id>/check")
@jwt_required()
def run_check(monitor_id: str):
    monitor = _owned_monitor(monitor_id)
    if monitor is None:
        return error_response("not_found", "Monitor not found.", 404)
    if not monitor.is_enabled:
        return error_response("monitor_disabled", "Enable this monitor before checking.", 409)
    # Intentionally synchronous for local use; workers will replace it in the next feature.
    result = scan_url(monitor.url)
    check = CheckResult(
        monitor_id=monitor.id,
        checked_at=result.checked_at,
        checked_url=monitor.url,
        status=result.status,
        status_code=result.status_code,
        response_time_ms=result.response_time_ms,
        certificate_expires_at=result.certificate_expires_at,
        final_url=result.final_url,
        error=result.error,
    )
    monitor.last_status = result.status
    monitor.last_checked_at = result.checked_at
    db.session.add(check)
    db.session.commit()
    return jsonify({"check": check.public_dict()}), 201


@bp.get("/<monitor_id>/checks")
@jwt_required()
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
