"""Portfolio showcase cannot touch user databases, scan URLs, or perform writes."""

from flask import Flask

from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, Monitor, SiteAudit
from sitesentinel.portfolio_demo import (
    DEMO_EMAIL,
    DEMO_PASSWORD,
    build_demo_app,
    main,
)
from tests.conftest import auth_header


def _login(client):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    assert response.status_code == 200, response.json
    return auth_header(response.json["access_token"])


def test_demo_is_ephemeral_and_disables_email_and_background_work(monkeypatch, tmp_path):
    existing_database = tmp_path / "existing.sqlite"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + str(existing_database))
    monkeypatch.setenv("JWT_SECRET_KEY", "should-not-be-used-in-a-demo")
    monkeypatch.setenv("SMTP_HOST", "smtp-not-for-demo.example.com")

    app = build_demo_app()
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
    assert app.config["JWT_SECRET_KEY"] != "should-not-be-used-in-a-demo"
    assert app.config["SMTP_HOST"] == ""
    assert not app.debug
    assert not existing_database.exists()

    with app.app_context():
        assert db.session.scalar(db.select(db.func.count()).select_from(Monitor)) == 3
        assert db.session.scalar(db.select(db.func.count()).select_from(SiteAudit)) == 4
        assert db.session.scalar(db.select(db.func.count()).select_from(AlertEvent)) == 3

    # A new showcase instance starts with its own independent history.
    another = build_demo_app()
    assert another is not app
    assert another.config["JWT_SECRET_KEY"] != app.config["JWT_SECRET_KEY"]


def test_showcase_reports_and_incidents_are_real_aggregations():
    app = build_demo_app()
    client = app.test_client()
    headers = _login(client)

    response = client.get("/api/v1/reports/overview", headers=headers)
    assert response.status_code == 200
    data = response.json
    assert data["monitors"]["total"] == 2
    assert data["monitors"]["last_status"]["up"] == 1
    assert data["monitors"]["last_status"]["down"] == 1
    assert data["checks"]["total"] == 10
    assert data["checks"]["up"] == 8
    assert data["checks"]["failed_or_blocked"] == 2
    assert data["checks"]["success_percentage"] == 80
    assert data["open_incidents"] == 1
    assert data["audits_in_window"] == 2

    incident_response = client.get("/api/v1/reports/incidents", headers=headers)
    assert incident_response.status_code == 200
    assert len(incident_response.json["incidents"]) == 1
    assert incident_response.json["incidents"][0]["status"] == "open"

    audit_response = client.get("/api/v1/reports/audit-highlights", headers=headers)
    assert audit_response.status_code == 200
    assert any(a["broken_links"] == 1 for a in audit_response.json["audits"])
    assert any(a["tracking_evidence"]["ga4"] for a in audit_response.json["audits"])

    workspaces = client.get("/api/v1/workspaces", headers=headers).json["workspaces"]
    assert len(workspaces) == 2
    team = next(w for w in workspaces if not w["is_personal"])
    team_report = client.get(
        f"/api/v1/workspaces/{team['id']}/reports/overview", headers=headers
    )
    assert team_report.status_code == 200
    assert team_report.json["monitors"]["total"] == 1
    assert team_report.json["open_incidents"] == 0
    assert len(
        client.get(
            f"/api/v1/workspaces/{team['id']}/reports/incidents", headers=headers
        ).json["incidents"]
    ) == 1
    assert client.get(
        f"/api/v1/workspaces/{team['id']}/reports/audit-highlights",
        headers=headers,
    ).json["audits"][0]["status"] == "completed"


def test_demo_rejects_state_changes_and_network_triggers(monkeypatch):
    app = build_demo_app()
    client = app.test_client()
    headers = _login(client)
    monitor = client.get("/api/v1/monitors", headers=headers).json["monitors"][0]
    mid = monitor["id"]

    # Any unintended remote call should fail the regression test.
    def forbidden_network(*_args, **_kwargs):
        raise AssertionError("No external network operations in the showcase")

    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", forbidden_network)
    monkeypatch.setattr("sitesentinel.api.audits.audit_site", forbidden_network)
    for method, path, payload in [
        ("post", "/api/v1/auth/register", {
            "email": "new@example.com", "display_name": "New account",
            "password": "test-only-password-123",
        }),
        ("post", "/api/v1/monitors", {"name": "New", "url": "https://example.com"}),
        ("post", f"/api/v1/monitors/{mid}/check", None),
        ("post", f"/api/v1/monitors/{mid}/audits", {"link_limit": 0}),
        ("patch", f"/api/v1/monitors/{mid}", {"name": "Hacked"}),
        ("delete", f"/api/v1/monitors/{mid}", None),
        ("post", "/api/v1/workspaces", {"name": "Another"}),
        ("patch", "/api/v1/users/me", {"display_name": "Changed"}),
        ("delete", "/api/v1/auth/logout", None),
    ]:
        result = getattr(client, method)(path, headers=headers, json=payload)
        assert result.status_code == 403, (method, path, result.json)
        assert result.json["error"]["code"] == "demo_read_only"

    # Read-only routes still require valid access JWT.
    assert client.get("/api/v1/reports/overview").status_code == 401
    assert client.get("/api/docs").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_demo_server_binds_only_to_loopback_with_no_debugger(monkeypatch):
    calls = []

    def run_server(_app, **options):
        calls.append(options)

    monkeypatch.setattr(Flask, "run", run_server)
    main(["--port", "8011"])
    assert calls == [{
        "host": "127.0.0.1", "port": 8011, "debug": False,
        "use_reloader": False, "threaded": False,
    }]
