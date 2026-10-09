"""Shared persistence and outage-transition logic for manual and scheduled checks."""

from datetime import timedelta

from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, CheckResult, Incident, Monitor
from sitesentinel.monitoring.scanner import ScanResult

FAILURES_BEFORE_INCIDENT = 2


def record_scan(monitor: Monitor, result: ScanResult) -> CheckResult:
    """Record a scan and emit *one* alert per outage/recovery transition.

    Caller owns the transaction. A failed transaction never leaves a partial alert.
    """
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
    db.session.add(check)
    monitor.last_status = result.status
    monitor.last_checked_at = result.checked_at
    monitor.next_check_at = result.checked_at + timedelta(seconds=monitor.interval_seconds)

    open_incident = db.session.scalar(
        db.select(Incident).where(Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None))
    )
    if result.status == "up":
        monitor.failure_streak = 0
        if open_incident is not None:
            open_incident.resolved_at = result.checked_at
            db.session.add(AlertEvent(monitor_id=monitor.id, incident=open_incident, kind="recovered"))
    else:
        monitor.failure_streak += 1
        if monitor.failure_streak >= FAILURES_BEFORE_INCIDENT and open_incident is None:
            incident = Incident(
                monitor_id=monitor.id,
                opened_at=result.checked_at,
                reason=(result.error or result.status)[:500],
            )
            db.session.add(incident)
            db.session.add(AlertEvent(monitor_id=monitor.id, incident=incident, kind="down"))
    return check
