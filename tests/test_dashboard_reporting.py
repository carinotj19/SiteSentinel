"""Dashboard reports: aggregate correctness, time windows, and tenant boundaries."""

from datetime import datetime, timedelta, timezone

from sitesentinel.extensions import db
from sitesentinel.models import CheckResult, Incident, Membership, Monitor, SiteAudit, User
from tests.conftest import auth_header

ROOT = "/api/v1/reports"


def _headers(tokens):
    return auth_header(tokens["access_token"])


def _monitor(client, headers, name, url):
    result = client.post(
        "/api/v1/monitors",
        headers=headers,
        json={"name": name, "url": url},
    )
    assert result.status_code == 201, result.json
    return db.session.get(Monitor, result.json["monitor"]["id"])


def _check(monitor, checked_at, status, latency=None):
    db.session.add(CheckResult(
        monitor_id=monitor.id,
        checked_url=monitor.url,
        checked_at=checked_at,
        status=status,
        status_code=200 if status == "up" else 503,
        response_time_ms=latency,
    ))


def _audit(monitor, when, *, completed=True):
    db.session.add(SiteAudit(
        monitor_id=monitor.id,
        audited_at=when,
        requested_url=monitor.url,
        final_url=monitor.url if completed else None,
        status="completed" if completed else "failed",
        status_code=200 if completed else 503,
        link_limit=5,
        error=None if completed else "HTTP 503",
        findings={
            "seo": {"issues": ["missing_h1", "noindex"]},
            "links": {"broken": [{"url": monitor.url + "missing"}]},
            "tracking": {"ga4": {"detected": True}, "gtm": {"detected": False}},
        } if completed else {},
    ))


def test_empty_reports_use_null_not_zero_percent(client, tokens):
    headers = _headers(tokens)
    response = client.get(ROOT + "/overview", headers=headers)
    assert response.status_code == 200, response.json
    data = response.json
    assert data["monitors"]["total"] == 0
    assert data["checks"]["total"] == 0
    assert data["checks"]["success_percentage"] is None
    assert data["checks"]["average_success_response_time_ms"] is None
    assert data["open_incidents"] == 0
    assert data["audits_in_window"] == 0
    for path, key in [
        ("/response-times", "points"),
        ("/incidents", "incidents"),
        ("/audit-highlights", "audits"),
    ]:
        res = client.get(ROOT + path, headers=headers)
        assert res.status_code == 200
        assert res.json[key] == []
        assert res.json["truncated"] is False


def test_overview_counts_real_samples_and_status(client, tokens):
    headers = _headers(tokens)
    healthy = _monitor(client, headers, "Healthy", "https://example.com/")
    failed = _monitor(client, headers, "Broken", "https://example.org/")
    inactive = _monitor(client, headers, "Disabled", "https://example.net/")

    healthy.last_status = "up"
    failed.last_status = "down"
    inactive.last_status = "blocked"
    inactive.is_enabled = False
    when = datetime.now(timezone.utc) - timedelta(minutes=3)
    _check(healthy, when, "up", 100)
    _check(healthy, when + timedelta(minutes=1), "up", 201)
    _check(failed, when, "down", 5000)
    _check(inactive, when, "blocked")
    _check(failed, when - timedelta(days=30), "down", 3000)
    db.session.add(Incident(monitor_id=failed.id, opened_at=when, reason="HTTP 503"))
    _audit(healthy, when)
    db.session.commit()

    response = client.get(ROOT + "/overview?days=7", headers=headers)
    assert response.status_code == 200
    overview = response.json
    assert overview["monitors"] == {
        "total": 3, "enabled": 2, "disabled": 1,
        "last_status": {"up": 1, "down": 1, "blocked": 1, "unknown": 0},
    }
    assert overview["checks"]["total"] == 4
    assert overview["checks"]["up"] == 2
    assert overview["checks"]["failed_or_blocked"] == 2
    assert overview["checks"]["success_percentage"] == 50
    assert overview["checks"]["average_success_response_time_ms"] == 150
    assert overview["open_incidents"] == 1
    assert overview["audits_in_window"] == 1
    assert "not a duration-weighted uptime" in overview["note"]


def test_response_trends_group_success_by_day_without_network(client, tokens):
    headers = _headers(tokens)
    one = _monitor(client, headers, "Alpha", "https://example.com/")
    two = _monitor(client, headers, "Beta", "https://example.org/")
    first = datetime.now(timezone.utc) - timedelta(days=2)
    second = datetime.now(timezone.utc) - timedelta(days=1)
    _check(one, first, "up", 100)
    _check(one, first + timedelta(minutes=1), "up", 200)
    _check(one, first + timedelta(minutes=2), "down", 6000)
    _check(two, second, "up", 50)
    db.session.commit()

    response = client.get(ROOT + "/response-times?days=7", headers=headers)
    assert response.status_code == 200, response.json
    data = response.json
    assert data["resolution"] == "day"
    assert len(data["points"]) == 2
    by_monitor = {p["monitor_id"]: p for p in data["points"]}
    assert by_monitor[one.id]["checks"] == 2
    assert by_monitor[one.id]["average_ms"] == 150
    assert by_monitor[one.id]["minimum_ms"] == 100
    assert by_monitor[one.id]["maximum_ms"] == 200
    assert by_monitor[two.id]["average_ms"] == 50
    short = client.get(ROOT + "/response-times?days=7&limit=1", headers=headers)
    assert len(short.json["points"]) == 1
    assert short.json["truncated"] is True


def test_incidents_include_active_and_recent_recoveries(client, tokens):
    headers = _headers(tokens)
    monitor = _monitor(client, headers, "Incident example", "https://example.com/")
    when = datetime.now(timezone.utc)
    db.session.add_all([
        Incident(monitor_id=monitor.id, opened_at=when - timedelta(days=40), reason="still down"),
        Incident(
            monitor_id=monitor.id, opened_at=when - timedelta(days=12),
            resolved_at=when - timedelta(days=1), reason="fixed yesterday",
        ),
        Incident(
            monitor_id=monitor.id, opened_at=when - timedelta(days=40),
            resolved_at=when - timedelta(days=35), reason="old outage",
        ),
    ])
    db.session.commit()
    result = client.get(ROOT + "/incidents?days=7", headers=headers)
    assert result.status_code == 200
    assert len(result.json["incidents"]) == 2
    assert {i["status"] for i in result.json["incidents"]} == {"open", "resolved"}
    assert all(i["monitor_name"] == "Incident example" for i in result.json["incidents"])
    limited = client.get(ROOT + "/incidents?days=7&limit=1", headers=headers)
    assert limited.json["truncated"] is True


def test_audit_highlights_do_not_claim_failed_audits_are_clean(client, tokens):
    headers = _headers(tokens)
    monitor = _monitor(client, headers, "SEO monitor", "https://example.com/")
    when = datetime.now(timezone.utc) - timedelta(minutes=5)
    _audit(monitor, when)
    _audit(monitor, when + timedelta(minutes=1), completed=False)
    db.session.commit()
    response = client.get(ROOT + "/audit-highlights", headers=headers)
    assert response.status_code == 200
    assert len(response.json["audits"]) == 2
    completed = next(item for item in response.json["audits"] if item["status"] == "completed")
    failed = next(item for item in response.json["audits"] if item["status"] == "failed")
    assert completed["seo_issue_codes"] == ["missing_h1", "noindex"]
    assert completed["broken_links"] == 1
    assert completed["tracking_evidence"] == {"ga4": True, "gtm": False, "meta_pixel": False}
    assert failed["seo_issue_codes"] is None
    assert failed["broken_links"] is None
    assert failed["tracking_evidence"] is None


def test_report_parameters_and_auth_are_checked(client, tokens):
    headers = _headers(tokens)
    assert client.get(ROOT + "/overview").status_code == 401
    for raw in ("0", "91", "abc", "-7", "1.5", "99999"):
        assert client.get(ROOT + "/overview?days=" + raw, headers=headers).status_code == 400
    for raw in ("0", "101", "what"):
        for endpoint in ("/incidents", "/audit-highlights", "/response-times"):
            assert client.get(ROOT + endpoint + "?limit=" + raw, headers=headers).status_code == 400
    assert client.get(ROOT + "/overview?admin=true", headers=headers).status_code == 400


def test_workspace_reports_are_readable_by_viewer_but_never_cross_tenant(client, tokens):
    owner = _headers(tokens)
    team_response = client.post(
        "/api/v1/workspaces", headers=owner, json={"name": "Operations"}
    )
    assert team_response.status_code == 201
    ws = team_response.json["workspace"]["id"]
    created = client.post(
        f"/api/v1/workspaces/{ws}/monitors",
        headers=owner,
        json={"name": "Shared", "url": "https://example.org/"},
    )
    assert created.status_code == 201
    shared = db.session.get(Monitor, created.json["monitor"]["id"])
    _check(shared, datetime.now(timezone.utc), "up", 123)
    db.session.commit()

    scope = f"/api/v1/workspaces/{ws}/reports"
    assert client.get(scope + "/overview", headers=owner).json["monitors"]["total"] == 1
    assert client.get(ROOT + "/overview", headers=owner).json["monitors"]["total"] == 0

    assert client.post("/api/v1/auth/register", json={
        "email": "viewer@example.com", "display_name": "Viewer",
        "password": "long-demo-password-123",
    }).status_code == 201
    viewer = db.session.scalar(db.select(User).where(User.email == "viewer@example.com"))
    db.session.add(Membership(workspace_id=ws, user_id=viewer.id, role="viewer"))
    db.session.commit()
    login = client.post("/api/v1/auth/login", json={
        "email": "viewer@example.com", "password": "long-demo-password-123",
    })
    viewer_headers = auth_header(login.json["access_token"])
    for route in ("/overview", "/response-times", "/incidents", "/audit-highlights"):
        assert client.get(scope + route, headers=viewer_headers).status_code == 200
    assert client.get(ROOT + "/overview", headers=viewer_headers).json["monitors"]["total"] == 0
    assert client.get(
        f"/api/v1/workspaces/{db.session.get(User, shared.user_id).id}/reports/overview",
        headers=viewer_headers,
    ).status_code == 404
    db.session.delete(db.session.scalar(
        db.select(Membership).where(Membership.workspace_id == ws, Membership.user_id == viewer.id)
    ))
    db.session.commit()
    for route in ("/overview", "/response-times", "/incidents", "/audit-highlights"):
        assert client.get(scope + route, headers=viewer_headers).status_code == 404
