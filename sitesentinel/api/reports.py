"""Read-only, tenant-scoped reporting over persisted monitoring and audit data.

Numbers are derived from stored samples, not real-time probes. In particular,
check success rate is not a duration-weighted uptime SLA.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from sqlalchemy import case, or_

from sitesentinel.api.common import error_response
from sitesentinel.extensions import db
from sitesentinel.models import CheckResult, Incident, Monitor, SiteAudit
from sitesentinel.workspaces import membership_for, personal_workspace_id

bp = Blueprint("reports", __name__, url_prefix="/api/v1/reports")


def _workspace(workspace_id):
    workspace_id = workspace_id or personal_workspace_id()
    if not workspace_id or membership_for(workspace_id) is None:
        return None, error_response("not_found", "Workspace not found.", 404)
    return workspace_id, None


def _positive_int(name: str, default: int, maximum: int) -> int | None:
    value = request.args.get(name, str(default))
    if not value.isascii() or not value.isdecimal() or len(value) > 4:
        return None
    number = int(value)
    return number if 1 <= number <= maximum else None


def _arguments(*, with_limit=False):
    allowed = {"days", "limit"} if with_limit else {"days"}
    if set(request.args) - allowed:
        return None, error_response("invalid_pagination", "Unsupported query parameter.", 400)
    days = _positive_int("days", 7, 90)
    limit = _positive_int("limit", 50, 100) if with_limit else None
    if days is None or (with_limit and limit is None):
        return None, error_response(
            "invalid_pagination", "Use days 1–90 and, where supported, limit 1–100.", 400
        )
    return (days, limit, datetime.now(timezone.utc) - timedelta(days=days)), None


def _scoped_checks(workspace_id, since):
    return (
        db.select(CheckResult)
        .join(Monitor, CheckResult.monitor_id == Monitor.id)
        .where(Monitor.workspace_id == workspace_id, CheckResult.checked_at >= since)
    )


@bp.get("/overview")
@jwt_required()
def overview(workspace_id=None):
    workspace_id, error = _workspace(workspace_id)
    if error is not None:
        return error
    args, error = _arguments()
    if error is not None:
        return error
    days, _, since = args

    states = db.session.execute(
        db.select(Monitor.is_enabled, Monitor.last_status, db.func.count())
        .where(Monitor.workspace_id == workspace_id)
        .group_by(Monitor.is_enabled, Monitor.last_status)
    ).all()
    status_counts = {"up": 0, "down": 0, "blocked": 0, "unknown": 0}
    enabled_count = 0
    monitor_count = 0
    for enabled, status, count in states:
        monitor_count += count
        enabled_count += count if enabled else 0
        status_counts[status if status in status_counts else "unknown"] += count

    total_checks, up_checks, average_ms = db.session.execute(
        db.select(
            db.func.count(CheckResult.id),
            db.func.sum(case((CheckResult.status == "up", 1), else_=0)),
            db.func.avg(case(
                (CheckResult.status == "up", CheckResult.response_time_ms),
                else_=None,
            )),
        )
        .join(Monitor, CheckResult.monitor_id == Monitor.id)
        .where(Monitor.workspace_id == workspace_id, CheckResult.checked_at >= since)
    ).one()
    up_checks = int(up_checks or 0)
    total_checks = int(total_checks)
    open_incidents = db.session.scalar(
        db.select(db.func.count(Incident.id))
        .join(Monitor, Incident.monitor_id == Monitor.id)
        .where(Monitor.workspace_id == workspace_id, Incident.resolved_at.is_(None))
    )
    audits_in_window = db.session.scalar(
        db.select(db.func.count(SiteAudit.id))
        .join(Monitor, SiteAudit.monitor_id == Monitor.id)
        .where(Monitor.workspace_id == workspace_id, SiteAudit.audited_at >= since)
    )
    return jsonify({
        "workspace_id": workspace_id,
        "window": {"days": days, "since": since.isoformat()},
        "monitors": {
            "total": monitor_count,
            "enabled": enabled_count,
            "disabled": monitor_count - enabled_count,
            "last_status": status_counts,
        },
        "checks": {
            "total": total_checks,
            "up": up_checks,
            "failed_or_blocked": total_checks - up_checks,
            "success_percentage": (
                round(100 * up_checks / total_checks, 2) if total_checks else None
            ),
            "average_success_response_time_ms": (
                round(float(average_ms)) if average_ms is not None else None
            ),
        },
        "open_incidents": open_incidents,
        "audits_in_window": audits_in_window,
        "note": (
            "Check success_percentage is the fraction of recorded up checks "
            "within the window, not a duration-weighted uptime guarantee."
        ),
    })


@bp.get("/response-times")
@jwt_required()
def response_times(workspace_id=None):
    workspace_id, error = _workspace(workspace_id)
    if error is not None:
        return error
    args, error = _arguments(with_limit=True)
    if error is not None:
        return error
    days, limit, since = args

    # Group by UTC date on PostgreSQL and SQLite. Only successful checks with
    # recorded latency contribute, avoiding timeout values skewing the trend.
    day = db.func.date(CheckResult.checked_at)
    rows = db.session.execute(
        db.select(
            Monitor.id,
            Monitor.name,
            day.label("date"),
            db.func.count(CheckResult.id),
            db.func.avg(CheckResult.response_time_ms),
            db.func.min(CheckResult.response_time_ms),
            db.func.max(CheckResult.response_time_ms),
        )
        .join(Monitor, Monitor.id == CheckResult.monitor_id)
        .where(
            Monitor.workspace_id == workspace_id,
            CheckResult.checked_at >= since,
            CheckResult.status == "up",
            CheckResult.response_time_ms.is_not(None),
        )
        .group_by(Monitor.id, Monitor.name, day)
        .order_by(day.desc(), Monitor.id.asc())
        .limit(limit + 1)
    ).all()
    return jsonify({
        "workspace_id": workspace_id,
        "window": {"days": days, "since": since.isoformat()},
        "resolution": "day",
        "truncated": len(rows) > limit,
        "points": [{
            "monitor_id": monitor_id,
            "monitor_name": name,
            "date": str(date),
            "checks": count,
            "average_ms": round(float(mean_ms)),
            "minimum_ms": minimum_ms,
            "maximum_ms": maximum_ms,
        } for monitor_id, name, date, count, mean_ms, minimum_ms, maximum_ms in rows[:limit]],
    })


@bp.get("/incidents")
@jwt_required()
def incidents(workspace_id=None):
    workspace_id, error = _workspace(workspace_id)
    if error is not None:
        return error
    args, error = _arguments(with_limit=True)
    if error is not None:
        return error
    days, limit, since = args
    rows = db.session.execute(
        db.select(Incident, Monitor.name)
        .join(Monitor, Monitor.id == Incident.monitor_id)
        .where(
            Monitor.workspace_id == workspace_id,
            or_(
                Incident.resolved_at.is_(None),
                Incident.opened_at >= since,
                Incident.resolved_at >= since,
            ),
        )
        .order_by(Incident.opened_at.desc(), Incident.id.desc())
        .limit(limit + 1)
    ).all()
    return jsonify({
        "workspace_id": workspace_id,
        "window": {"days": days, "since": since.isoformat()},
        "truncated": len(rows) > limit,
        "incidents": [
            {**incident.public_dict(), "monitor_name": name}
            for incident, name in rows[:limit]
        ],
    })


@bp.get("/audit-highlights")
@jwt_required()
def audit_highlights(workspace_id=None):
    workspace_id, error = _workspace(workspace_id)
    if error is not None:
        return error
    args, error = _arguments(with_limit=True)
    if error is not None:
        return error
    days, limit, since = args
    rows = db.session.execute(
        db.select(SiteAudit, Monitor.name)
        .join(Monitor, Monitor.id == SiteAudit.monitor_id)
        .where(Monitor.workspace_id == workspace_id, SiteAudit.audited_at >= since)
        .order_by(SiteAudit.audited_at.desc(), SiteAudit.id.desc())
        .limit(limit + 1)
    ).all()
    items = []
    for audit, name in rows[:limit]:
        item = audit.public_dict()
        item["monitor_name"] = name
        if audit.status == "completed":
            findings = audit.findings or {}
            seo = findings.get("seo") or {}
            links = findings.get("links") or {}
            tracking = findings.get("tracking") or {}
            item["seo_issue_codes"] = seo.get("issues") or []
            item["broken_links"] = len(links.get("broken") or [])
            item["tracking_evidence"] = {
                source: bool((tracking.get(source) or {}).get("detected"))
                for source in ("ga4", "gtm", "meta_pixel")
            }
        else:
            # A failed HTML fetch is not a clean audit.
            item["seo_issue_codes"] = None
            item["broken_links"] = None
            item["tracking_evidence"] = None
        items.append(item)
    return jsonify({
        "workspace_id": workspace_id,
        "window": {"days": days, "since": since.isoformat()},
        "truncated": len(rows) > limit,
        "audits": items,
    })
