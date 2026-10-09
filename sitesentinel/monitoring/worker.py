"""Database-backed periodic scanning and optional SMTP alert delivery.

Run as a separate Flask CLI process, not inside the web server. This keeps the
local development workflow functional on Windows without Docker or Redis.
"""

import logging
import smtplib
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from uuid import uuid4

import click
from flask import current_app
from sqlalchemy import or_, update

from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, Monitor, User
from sitesentinel.monitoring.scanner import scan_url
from sitesentinel.monitoring.service import record_scan

logger = logging.getLogger(__name__)
MONITOR_LEASE_SECONDS = 120
ALERT_LEASE_SECONDS = 60
MAX_EMAIL_ATTEMPTS = 3


def utcnow():
    return datetime.now(timezone.utc)


def run_due_checks(limit: int = 10, now: datetime | None = None) -> int:
    """Claim and process due monitors. One scan result is committed per claim.

    A lease allows the next poll to recover scans abandoned by a crashed worker.
    SQLite supports this for local use; PostgreSQL is recommended for multi-worker
    deployment and monitoring quotas are required before public exposure.
    """
    now = now or utcnow()
    ids = db.session.scalars(
        db.select(Monitor.id).where(
            Monitor.is_enabled.is_(True),
            Monitor.next_check_at <= now,
            or_(Monitor.lease_expires_at.is_(None), Monitor.lease_expires_at <= now),
        ).order_by(Monitor.next_check_at.asc()).limit(limit)
    ).all()
    db.session.rollback()  # Release the read transaction before attempting a lease.
    processed = 0
    for monitor_id in ids:
        token = str(uuid4())
        claim = db.session.execute(
            update(Monitor).where(
                Monitor.id == monitor_id, Monitor.is_enabled.is_(True),
                Monitor.next_check_at <= now,
                or_(Monitor.lease_expires_at.is_(None), Monitor.lease_expires_at <= now),
            ).values(lease_token=token, lease_expires_at=now + timedelta(seconds=MONITOR_LEASE_SECONDS))
        )
        db.session.commit()
        if not claim.rowcount:
            continue
        monitor = db.session.get(Monitor, monitor_id)
        url = monitor.url
        db.session.rollback()
        try:
            result = scan_url(url)
        except Exception:
            # Unexpected scanner bugs should be retried, not recorded as a false outage.
            logger.exception("Unexpected scanner exception for monitor %s", monitor_id)
            db.session.execute(
                update(Monitor).where(Monitor.id == monitor_id, Monitor.lease_token == token)
                .values(lease_token=None, lease_expires_at=None,
                        next_check_at=utcnow() + timedelta(seconds=60))
            )
            db.session.commit()
            continue
        # Reload after the network call. URL edits/deletions revoke this lease.
        db.session.expire_all()
        monitor = db.session.get(Monitor, monitor_id)
        if monitor is None or monitor.lease_token != token or not monitor.is_enabled or monitor.url != url:
            db.session.rollback()
            continue
        record_scan(monitor, result)
        monitor.lease_token = None
        monitor.lease_expires_at = None
        db.session.commit()
        processed += 1
    return processed


def _send_email(event: AlertEvent) -> None:
    config = current_app.config
    recipient = db.session.get(User, event.monitor.user_id).email
    subject = ("SiteSentinel: website down" if event.kind == "down"
               else "SiteSentinel: website recovered")
    msg = EmailMessage()
    msg["From"] = config["SMTP_FROM"]
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(
        f"Monitor: {event.monitor.name}\nURL: {event.monitor.url}\n"
        f"Event: {event.kind}\nRecorded: {event.created_at.isoformat()}\n"
        "\nThis is an automated SiteSentinel notification.\n"
    )
    with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=10) as smtp:
        if config["SMTP_STARTTLS"]:
            smtp.starttls(context=ssl.create_default_context())
        if config.get("SMTP_USERNAME"):
            smtp.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        smtp.send_message(msg)


def dispatch_alerts(limit: int = 10, now: datetime | None = None) -> int:
    """Attempt queued email alerts, with at-least-once retry semantics.

    Without SMTP_HOST events stay pending and remain visible in the API.
    """
    if not current_app.config.get("SMTP_HOST"):
        return 0
    now = now or utcnow()
    events = db.session.scalars(
        db.select(AlertEvent.id).where(
            or_(AlertEvent.delivery_status == "pending",
                (AlertEvent.delivery_status == "sending") & (AlertEvent.next_attempt_at <= now)),
            or_(AlertEvent.next_attempt_at.is_(None), AlertEvent.next_attempt_at <= now),
        ).order_by(AlertEvent.created_at.asc()).limit(limit)
    ).all()
    db.session.rollback()
    dispatched = 0
    for event_id in events:
        claim = db.session.execute(
            update(AlertEvent).where(
                AlertEvent.id == event_id,
                or_(AlertEvent.delivery_status == "pending",
                    (AlertEvent.delivery_status == "sending") & (AlertEvent.next_attempt_at <= now)),
                or_(AlertEvent.next_attempt_at.is_(None), AlertEvent.next_attempt_at <= now),
            ).values(delivery_status="sending", next_attempt_at=now + timedelta(seconds=ALERT_LEASE_SECONDS))
        )
        db.session.commit()
        if not claim.rowcount:
            continue
        event = db.session.get(AlertEvent, event_id)
        try:
            _send_email(event)
        except (OSError, smtplib.SMTPException, ValueError) as exc:
            logger.warning("SMTP delivery failed for alert %s: %s", event_id, type(exc).__name__)
            event.attempts += 1
            event.last_error = type(exc).__name__[:400]
            if event.attempts >= MAX_EMAIL_ATTEMPTS:
                event.delivery_status = "failed"
                event.next_attempt_at = None
            else:
                event.delivery_status = "pending"
                event.next_attempt_at = utcnow() + timedelta(seconds=60 * 2 ** (event.attempts - 1))
        else:
            event.attempts += 1
            event.delivery_status = "sent"
            event.next_attempt_at = None
            event.delivered_at = utcnow()
            event.last_error = None
            dispatched += 1
        db.session.commit()
    return dispatched


@click.command("monitor-worker")
@click.option("--once", is_flag=True, help="Process one batch and exit.")
@click.option("--poll-seconds", type=click.IntRange(2, 3600), default=10, show_default=True)
@click.option("--batch-size", type=click.IntRange(1, 50), default=10, show_default=True)
def worker_command(once: bool, poll_seconds: int, batch_size: int) -> None:
    """Run periodic website checks and queued email alerts in a separate process."""
    click.echo("SiteSentinel worker started (Ctrl+C to stop).")
    try:
        while True:
            try:
                scans = run_due_checks(limit=batch_size)
                emails = dispatch_alerts(limit=batch_size)
                if scans or emails:
                    click.echo(f"Processed {scans} scans and sent {emails} emails.")
            except Exception:
                db.session.rollback()
                logger.exception("Worker iteration failed; retrying on next poll")
                if once:
                    raise click.ClickException("Worker iteration failed; see server logs") from None
            if once:
                break
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        click.echo("Worker stopped.")
