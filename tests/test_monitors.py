"""Authenticated website CRUD, isolation, and persisted check history."""

from datetime import datetime, timezone

from sitesentinel.monitoring.scanner import ScanResult
from tests.conftest import auth_header

ROOT = "/api/v1/monitors"


def _headers(tokens):
    return auth_header(tokens["access_token"])


def _new_monitor(client, tokens, name="My site", url="https://example.com"):
    response = client.post(ROOT, json={"name": name, "url": url}, headers=_headers(tokens))
    assert response.status_code == 201, response.json
    return response.json["monitor"]


def test_monitors_require_authorization(client):
    assert client.get(ROOT).status_code == 401
    assert client.post(ROOT, json={"name": "Test", "url": "https://example.com"}).status_code == 401


def test_create_read_update_and_delete(client, tokens):
    monitor = _new_monitor(client, tokens)
    assert monitor["url"] == "https://example.com/"
    assert monitor["last_status"] is None
    assert monitor["is_enabled"]
    monitor_id = monitor["id"]
    list_response = client.get(ROOT, headers=_headers(tokens))
    assert list_response.status_code == 200
    assert list_response.json["total"] == 1
    assert list_response.json["monitors"][0]["id"] == monitor_id
    assert client.get(f"{ROOT}/{monitor_id}", headers=_headers(tokens)).status_code == 200
    changed = client.patch(
        f"{ROOT}/{monitor_id}", headers=_headers(tokens), json={"name": "Renamed", "is_enabled": False}
    )
    assert changed.status_code == 200
    assert changed.json["monitor"]["name"] == "Renamed"
    assert not changed.json["monitor"]["is_enabled"]
    assert client.post(f"{ROOT}/{monitor_id}/check", headers=_headers(tokens)).status_code == 409
    assert client.delete(f"{ROOT}/{monitor_id}", headers=_headers(tokens)).status_code == 204
    assert client.get(f"{ROOT}/{monitor_id}", headers=_headers(tokens)).status_code == 404


def test_duplicate_and_invalid_urls(client, tokens):
    _new_monitor(client, tokens)
    duplicate = client.post(ROOT, headers=_headers(tokens), json={"name": "Dup", "url": "https://example.com/"})
    assert duplicate.status_code == 409
    assert duplicate.json["error"]["code"] == "duplicate_monitor"
    for bad_url in ["http://127.0.0.1", "http://169.254.169.254", "file:///etc/passwd", "https://localhost"]:
        response = client.post(ROOT, headers=_headers(tokens), json={"name": "No", "url": bad_url})
        assert response.status_code == 400
    assert client.post(ROOT, headers=_headers(tokens), json={"name": "X"}).status_code == 400
    assert client.post(ROOT, headers=_headers(tokens), json={"name": "X", "url": "https://example.org", "user_id": "spoof"}).status_code == 400
    assert client.post(ROOT, headers=_headers(tokens), json={"name": "X", "url": "https://example.org", "is_enabled": "yes"}).status_code == 400


def test_owner_isolation_returns_404(client, tokens):
    mine = _new_monitor(client, tokens)
    response = client.post("/api/v1/auth/register", json={
        "email": "bob@example.com", "display_name": "Bob", "password": "a-long-password-for-bob-1"
    })
    assert response.status_code == 201
    login = client.post("/api/v1/auth/login", json={"email": "bob@example.com", "password": "a-long-password-for-bob-1"})
    assert login.status_code == 200
    other = auth_header(login.json["access_token"])
    assert client.get(ROOT, headers=other).json["total"] == 0
    for method, route in [
        ("get", f"{ROOT}/{mine['id']}"),
        ("patch", f"{ROOT}/{mine['id']}"),
        ("delete", f"{ROOT}/{mine['id']}"),
        ("post", f"{ROOT}/{mine['id']}/check"),
        ("get", f"{ROOT}/{mine['id']}/checks"),
    ]:
        response = getattr(client, method)(route, headers=other, **({"json": {"name": "Bad"}} if method == "patch" else {}))
        assert response.status_code == 404


def test_check_persists_status_and_certificate(client, tokens, monkeypatch):
    monitor = _new_monitor(client, tokens)
    expiry = datetime(2027, 5, 1, tzinfo=timezone.utc)
    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda _url: ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200,
        response_time_ms=123, certificate_expires_at=expiry, final_url="https://example.com/"
    ))
    resp = client.post(f"{ROOT}/{monitor['id']}/check", headers=_headers(tokens))
    assert resp.status_code == 201
    assert resp.json["check"]["status"] == "up"
    assert resp.json["check"]["checked_url"] == "https://example.com/"
    assert resp.json["check"]["status_code"] == 200
    assert resp.json["check"]["response_time_ms"] == 123
    assert resp.json["check"]["certificate_expires_at"].startswith("2027-05-01")
    latest = client.get(f"{ROOT}/{monitor['id']}", headers=_headers(tokens)).json["monitor"]
    assert latest["last_status"] == "up"
    assert latest["last_checked_at"]
    checks = client.get(f"{ROOT}/{monitor['id']}/checks", headers=_headers(tokens)).json["checks"]
    assert len(checks) == 1
    assert checks[0]["id"] == resp.json["check"]["id"]


def test_scan_failure_and_dns_block_persist(client, tokens, monkeypatch):
    monitor = _new_monitor(client, tokens)
    for status in ("down", "blocked"):
        monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda _url, status=status: ScanResult(
            status=status, checked_at=datetime.now(timezone.utc), error="Unsafe target" if status == "blocked" else "timeout"
        ))
        result = client.post(f"{ROOT}/{monitor['id']}/check", headers=_headers(tokens))
        assert result.status_code == 201 and result.json["check"]["status"] == status
    assert len(client.get(f"{ROOT}/{monitor['id']}/checks", headers=_headers(tokens)).json["checks"]) == 2
    assert client.get(f"{ROOT}/{monitor['id']}", headers=_headers(tokens)).json["monitor"]["last_status"] == "blocked"


def test_pagination_validation(client, tokens):
    for n in range(3):
        _new_monitor(client, tokens, name=f"Site {n}", url=f"https://example.com/a/{n}")
    response = client.get(f"{ROOT}?limit=2&offset=1", headers=_headers(tokens))
    assert response.status_code == 200
    assert len(response.json["monitors"]) == 2
    assert response.json["total"] == 3
    assert client.get(f"{ROOT}?limit=999", headers=_headers(tokens)).status_code == 400
    assert client.get(f"{ROOT}?offset=-1", headers=_headers(tokens)).status_code == 400
    monitor = client.get(ROOT, headers=_headers(tokens)).json["monitors"][0]
    assert client.get(f"{ROOT}/{monitor['id']}/checks?limit=0", headers=_headers(tokens)).status_code == 400


def test_check_history_deleted_with_monitor(client, tokens, monkeypatch):
    from sitesentinel.extensions import db
    from sitesentinel.models import CheckResult

    monitor = _new_monitor(client, tokens)
    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda _url: ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200
    ))
    response = client.post(f"{ROOT}/{monitor['id']}/check", headers=_headers(tokens))
    check_id = response.json["check"]["id"]
    assert db.session.get(CheckResult, check_id) is not None
    assert client.delete(f"{ROOT}/{monitor['id']}", headers=_headers(tokens)).status_code == 204
    assert db.session.get(CheckResult, check_id) is None


def test_changing_url_resets_cached_status_but_preserves_labeled_history(client, tokens, monkeypatch):
    monitor = _new_monitor(client, tokens)
    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda _url: ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200
    ))
    assert client.post(f"{ROOT}/{monitor['id']}/check", headers=_headers(tokens)).status_code == 201
    updated = client.patch(
        f"{ROOT}/{monitor['id']}", headers=_headers(tokens), json={"url": "https://example.net/"}
    )
    assert updated.status_code == 200
    assert updated.json["monitor"]["last_status"] is None
    history = client.get(f"{ROOT}/{monitor['id']}/checks", headers=_headers(tokens)).json["checks"]
    assert history[0]["checked_url"] == "https://example.com/"
    assert client.get(f"{ROOT}?offset={'9' * 100}", headers=_headers(tokens)).status_code == 400
