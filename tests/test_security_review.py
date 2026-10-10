"""Security regression cases found during the portfolio readiness review."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from sitesentinel.extensions import db
from sitesentinel.models import (
    AlertEvent,
    CheckResult,
    Incident,
    Membership,
    Monitor,
    SiteAudit,
    User,
)
from sitesentinel.monitoring.scanner import ScanResult
from sitesentinel.monitoring.worker import dispatch_alerts, run_due_checks
from tests.conftest import auth_header


def _monitor(client, tokens, path="/"):
    headers = auth_header(tokens["access_token"])
    created = client.post("/api/v1/monitors", headers=headers, json={
        "name": "Security test", "url": "https://example.com" + path,
    })
    assert created.status_code == 201, created.json
    return created.json["monitor"]


def _fake_result(url):
    return ScanResult(
        status="up", status_code=200, checked_at=datetime.now(timezone.utc), final_url=url,
    )


def test_manual_check_refuses_an_existing_worker_lease(client, tokens, monkeypatch):
    monitor = _monitor(client, tokens)
    site = db.session.get(Monitor, monitor["id"])
    site.lease_token = "another-worker-owns-this"
    site.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
    db.session.commit()

    def unexpected(_url):
        raise AssertionError("A leased monitor must not be scanned.")

    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", unexpected)
    response = client.post(
        f"/api/v1/monitors/{site.id}/check",
        headers=auth_header(tokens["access_token"]),
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "check_in_progress"
    assert db.session.scalar(db.select(db.func.count()).select_from(CheckResult)) == 0


def test_manual_check_excludes_worker_and_discards_edited_url(client, tokens, monkeypatch):
    monitor = _monitor(client, tokens)
    monitor_id = monitor["id"]

    def scan_then_edit(url):
        # The worker must not check a URL already reserved for manual scanning.
        assert run_due_checks(now=datetime.now(timezone.utc) + timedelta(seconds=1)) == 0
        site = db.session.get(Monitor, monitor_id)
        assert site.lease_token
        assert site.url == url
        db.session.execute(
            update(Monitor).where(Monitor.id == monitor_id)
            .values(url="https://example.org/", lease_token=None, lease_expires_at=None)
        )
        db.session.commit()
        return _fake_result(url)

    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", scan_then_edit)
    response = client.post(
        f"/api/v1/monitors/{monitor_id}/check",
        headers=auth_header(tokens["access_token"]),
    )
    assert response.status_code == 409
    assert response.json["error"]["code"] == "check_changed"
    assert db.session.scalar(db.select(db.func.count()).select_from(CheckResult)) == 0
    assert db.session.get(Monitor, monitor_id).url == "https://example.org/"


def test_manual_check_releases_lease_when_scanner_crashes(client, tokens, monkeypatch):
    monitor = _monitor(client, tokens)

    def unexpected(_url):
        raise RuntimeError("Unexpected network adapter bug")

    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", unexpected)
    response = client.post(
        f"/api/v1/monitors/{monitor['id']}/check",
        headers=auth_header(tokens["access_token"]),
    )
    assert response.status_code == 503
    site = db.session.get(Monitor, monitor["id"])
    assert site.lease_token is None
    assert site.lease_expires_at is None
    assert db.session.scalar(db.select(db.func.count()).select_from(CheckResult)) == 0


def test_manual_check_revalidates_workspace_membership_after_network(client, tokens, monkeypatch):
    owner = auth_header(tokens["access_token"])
    team = client.post("/api/v1/workspaces", headers=owner, json={"name": "Security Team"})
    workspace_id = team.json["workspace"]["id"]

    assert client.post("/api/v1/auth/register", json={
        "email": "member@example.com",
        "display_name": "Team member",
        "password": "member-long-password-123",
    }).status_code == 201
    user = db.session.scalar(db.select(User).where(User.email == "member@example.com"))
    member = Membership(workspace_id=workspace_id, user_id=user.id, role="member")
    db.session.add(member)
    db.session.commit()
    login = client.post("/api/v1/auth/login", json={
        "email": "member@example.com", "password": "member-long-password-123"
    })
    headers = auth_header(login.json["access_token"])
    site = client.post(
        f"/api/v1/workspaces/{workspace_id}/monitors",
        headers=headers, json={"name": "Team site", "url": "https://example.org/"},
    )
    assert site.status_code == 201
    mid = site.json["monitor"]["id"]

    def scan_after_removal(url):
        db.session.delete(member)
        db.session.commit()
        return _fake_result(url)

    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", scan_after_removal)
    result = client.post(
        f"/api/v1/workspaces/{workspace_id}/monitors/{mid}/check",
        headers=headers,
    )
    assert result.status_code == 404
    assert db.session.scalar(db.select(db.func.count()).select_from(CheckResult)) == 0
    assert db.session.get(Monitor, mid).lease_token is None


def test_audit_does_not_persist_stale_configuration(client, tokens, monkeypatch):
    monitor = _monitor(client, tokens)
    mid = monitor["id"]

    def changed_audit(url, link_limit):
        db.session.execute(
            update(Monitor).where(Monitor.id == mid).values(url="https://example.org/")
        )
        db.session.commit()
        return {
            "status": "completed", "status_code": 200, "final_url": url,
            "findings": {"seo": {"title": "Outdated"}}, "error": None,
        }

    monkeypatch.setattr("sitesentinel.api.audits.audit_site", changed_audit)
    result = client.post(
        f"/api/v1/monitors/{mid}/audits",
        headers=auth_header(tokens["access_token"]), json={"link_limit": 0},
    )
    assert result.status_code == 409
    assert db.session.scalar(db.select(db.func.count()).select_from(SiteAudit)) == 0


def test_removed_workspace_creator_no_longer_receives_alert(app, client, tokens, monkeypatch):
    owner_headers = auth_header(tokens["access_token"])
    workspace = client.post(
        "/api/v1/workspaces", headers=owner_headers, json={"name": "Alerts"}
    ).json["workspace"]
    assert client.post("/api/v1/auth/register", json={
        "email": "former@example.com", "display_name": "Former member",
        "password": "long-former-password-123",
    }).status_code == 201
    former = db.session.scalar(db.select(User).where(User.email == "former@example.com"))
    membership = Membership(
        workspace_id=workspace["id"], user_id=former.id, role="member",
    )
    db.session.add(membership)
    site = Monitor(
        workspace_id=workspace["id"], user_id=former.id,
        name="Private team site", url="https://example.com/",
        is_enabled=True, interval_seconds=300, failure_streak=0,
    )
    db.session.add(site)
    db.session.flush()
    incident = Incident(monitor_id=site.id, opened_at=datetime.now(timezone.utc), reason="Down")
    db.session.add(incident)
    db.session.flush()
    alert = AlertEvent(monitor_id=site.id, incident_id=incident.id, kind="down")
    db.session.add(alert)
    db.session.commit()

    db.session.delete(membership)
    db.session.commit()
    app.config.update({"SMTP_HOST": "smtp.example.com"})

    def should_never_open_smtp(*_args, **_kwargs):
        raise AssertionError("Revoked team members must not receive SMTP alerts")

    monkeypatch.setattr("sitesentinel.monitoring.worker.smtplib.SMTP", should_never_open_smtp)
    assert dispatch_alerts() == 0
    db.session.refresh(alert)
    assert alert.delivery_status == "failed"
    assert alert.last_error == "recipient_not_authorized"
    assert alert.attempts == 1


def test_deactivated_monitor_creator_does_not_receive_alert(app, client, tokens, monkeypatch):
    monitor = _monitor(client, tokens)
    site = db.session.get(Monitor, monitor["id"])
    incident = Incident(monitor_id=site.id, opened_at=datetime.now(timezone.utc), reason="Down")
    db.session.add(incident)
    db.session.flush()
    alert = AlertEvent(monitor_id=site.id, incident_id=incident.id, kind="down")
    db.session.add(alert)
    db.session.commit()
    user = db.session.get(User, site.user_id)
    user.is_active = False
    db.session.commit()
    app.config["SMTP_HOST"] = "smtp.example.com"

    def should_never_open_smtp(*_args, **_kwargs):
        raise AssertionError("Inactive users must not receive alerts")

    monkeypatch.setattr("sitesentinel.monitoring.worker.smtplib.SMTP", should_never_open_smtp)
    assert dispatch_alerts() == 0
    db.session.refresh(alert)
    assert alert.delivery_status == "failed"
