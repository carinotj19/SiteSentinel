"""Background scheduling, incident lifecycle, authorization, and email retries."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, Monitor
from sitesentinel.monitoring.scanner import ScanResult
from sitesentinel.monitoring.worker import dispatch_alerts, run_due_checks
from tests.conftest import auth_header

ROOT = "/api/v1/monitors"


def new_monitor(client, tokens, **overrides):
    payload = {"name": "Example", "url": f"https://example.com/{uuid4()}"}
    payload.update(overrides)
    res = client.post(ROOT, json=payload, headers=auth_header(tokens["access_token"]))
    assert res.status_code == 201, res.json
    return res.json["monitor"]


def scheduled(monkeypatch, monitor, status, *, at=None):
    when = at or datetime.now(timezone.utc)
    monkeypatch.setattr("sitesentinel.monitoring.worker.scan_url", lambda url: ScanResult(
        status=status, checked_at=when, status_code=200 if status == "up" else 503,
        final_url=url, error="HTTP 503" if status != "up" else None,
    ))
    instance = db.session.get(Monitor, monitor["id"])
    instance.next_check_at = when - timedelta(seconds=1)
    db.session.commit()
    assert run_due_checks(now=when, limit=10) == 1


def test_monitor_schedule_validation(client, tokens):
    headers = auth_header(tokens["access_token"])
    for interval in (0, 59, 86401, True, "300", -5):
        resp = client.post(ROOT, json={
            "name": "Invalid", "url": "https://example.com", "interval_seconds": interval
        }, headers=headers)
        assert resp.status_code == 400
    created = new_monitor(client, tokens, interval_seconds=60)
    assert created["interval_seconds"] == 60
    assert created["next_check_at"] is not None
    updated = client.patch(f"{ROOT}/{created['id']}", json={"interval_seconds": 600}, headers=headers)
    assert updated.status_code == 200
    assert updated.json["monitor"]["interval_seconds"] == 600
    assert client.patch(f"{ROOT}/{created['id']}", json={"interval_seconds": False}, headers=headers).status_code == 400


def test_worker_runs_when_due_and_saves_history(client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens)
    now = datetime.now(timezone.utc) + timedelta(seconds=10)
    scheduled(monkeypatch, monitor, "up", at=now)
    instance = db.session.get(Monitor, monitor["id"])
    assert instance.failure_streak == 0
    assert instance.lease_token is None
    assert instance.next_check_at is not None
    assert run_due_checks(now=now) == 0
    history = client.get(f"{ROOT}/{monitor['id']}/checks", headers=auth_header(tokens["access_token"]))
    assert len(history.json["checks"]) == 1
    assert history.json["checks"][0]["status"] == "up"


def test_outage_and_recovery_emit_single_transition(client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens, interval_seconds=60)
    now = datetime.now(timezone.utc)
    for count in range(3):
        scheduled(monkeypatch, monitor, "down", at=now + timedelta(minutes=count))
    headers = auth_header(tokens["access_token"])
    incidents = client.get(f"{ROOT}/{monitor['id']}/incidents", headers=headers)
    alerts = client.get(f"{ROOT}/{monitor['id']}/alerts", headers=headers)
    assert incidents.status_code == 200 and len(incidents.json["incidents"]) == 1
    assert incidents.json["incidents"][0]["status"] == "open"
    assert [a["kind"] for a in alerts.json["alerts"]] == ["down"]
    scheduled(monkeypatch, monitor, "up", at=now + timedelta(minutes=3))
    assert client.get(f"{ROOT}/{monitor['id']}/incidents", headers=headers).json["incidents"][0]["status"] == "resolved"
    kinds = [a["kind"] for a in client.get(f"{ROOT}/{monitor['id']}/alerts", headers=headers).json["alerts"]]
    assert sorted(kinds) == ["down", "recovered"]
    assert db.session.get(Monitor, monitor["id"]).failure_streak == 0


def test_disabled_monitor_skipped_and_schedule_resumes(client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens, is_enabled=False)
    instance = db.session.get(Monitor, monitor["id"])
    assert instance.next_check_at is None
    assert run_due_checks() == 0
    resp = client.patch(f"{ROOT}/{monitor['id']}", json={"is_enabled": True},
                        headers=auth_header(tokens["access_token"]))
    assert resp.status_code == 200 and resp.json["monitor"]["next_check_at"]
    monkeypatch.setattr("sitesentinel.monitoring.worker.scan_url", lambda url: ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200, final_url=url,
    ))
    assert run_due_checks(now=datetime.now(timezone.utc) + timedelta(seconds=2)) == 1


def test_incidents_and_alerts_are_owner_scoped(client, tokens):
    monitor = new_monitor(client, tokens)
    client.post("/api/v1/auth/register", json={
        "email": "second@example.com", "display_name": "Second", "password": "a-long-test-password-123",
    })
    token = client.post("/api/v1/auth/login", json={
        "email": "second@example.com", "password": "a-long-test-password-123",
    }).json["access_token"]
    for endpoint in ("incidents", "alerts"):
        assert client.get(f"{ROOT}/{monitor['id']}/{endpoint}", headers=auth_header(token)).status_code == 404
        assert client.get(f"{ROOT}/{monitor['id']}/{endpoint}").status_code == 401


def test_smtp_disabled_keeps_events_pending(client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens)
    now = datetime.now(timezone.utc)
    scheduled(monkeypatch, monitor, "down", at=now)
    scheduled(monkeypatch, monitor, "down", at=now + timedelta(minutes=1))
    assert dispatch_alerts() == 0
    events = db.session.scalars(db.select(AlertEvent).filter_by(monitor_id=monitor["id"])).all()
    assert len(events) == 1 and events[0].delivery_status == "pending"


def test_smtp_success_and_retry(app, client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens)
    now = datetime.now(timezone.utc)
    scheduled(monkeypatch, monitor, "down", at=now)
    scheduled(monkeypatch, monitor, "down", at=now + timedelta(minutes=1))
    app.config.update({"SMTP_HOST": "localhost", "SMTP_PORT": 2525, "SMTP_FROM": "alert@example.com"})
    calls = []

    def fail_once(event):
        calls.append(event.id)
        if len(calls) == 1:
            raise OSError("server unavailable")
    monkeypatch.setattr("sitesentinel.monitoring.worker._send_email", fail_once)
    assert dispatch_alerts() == 0
    alert = db.session.scalar(db.select(AlertEvent).filter_by(monitor_id=monitor["id"]))
    assert alert.delivery_status == "pending" and alert.attempts == 1
    assert dispatch_alerts(now=utc_future(90)) == 1
    assert alert.delivery_status == "sent" and alert.attempts == 2
    assert dispatch_alerts(now=utc_future(120)) == 0
    assert len(calls) == 2


def utc_future(seconds):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def test_monitor_edit_revokes_due_lease_and_resolves_outage(client, tokens, monkeypatch):
    monitor = new_monitor(client, tokens)
    now = datetime.now(timezone.utc)
    scheduled(monkeypatch, monitor, "down", at=now)
    scheduled(monkeypatch, monitor, "down", at=now + timedelta(minutes=5))
    headers = auth_header(tokens["access_token"])
    resp = client.patch(f"{ROOT}/{monitor['id']}", headers=headers, json={"url": "https://other.example.com/"})
    assert resp.status_code == 200
    assert resp.json["monitor"]["failure_streak"] == 0
    assert client.get(f"{ROOT}/{monitor['id']}/incidents", headers=headers).json["incidents"][0]["status"] == "resolved"
