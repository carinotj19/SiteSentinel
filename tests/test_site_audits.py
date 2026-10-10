"""Static website audits: bounded parsing, persistence, and workspace access."""

from datetime import datetime, timezone

import pytest

from sitesentinel.auditing import engine
from sitesentinel.extensions import db
from sitesentinel.models import Membership, Monitor, SiteAudit, User
from sitesentinel.monitoring.scanner import ScanResult, UnsafeTarget
from tests.conftest import auth_header

ROOT = "/api/v1/monitors"


def make_monitor(client, tokens, path="/"):
    response = client.post(
        ROOT,
        headers=auth_header(tokens["access_token"]),
        json={"name": "Website", "url": "https://example.com" + path},
    )
    assert response.status_code == 201, response.json
    return response.json["monitor"]


def fake_audit(url, link_limit):
    return {
        "status": "completed", "final_url": url, "status_code": 200,
        "findings": {
            "seo": {"title": "Example", "issues": []},
            "tracking": {"ga4": {"detected": True, "ids": ["G-ABCD1234"]}},
            "links": {"checked": link_limit, "broken": []},
        },
        "error": None,
    }


def test_static_html_seo_tracking_and_broken_links(monkeypatch):
    visited = []

    def fake_scan(url):
        visited.append(url)
        return ScanResult(
            status="down" if url.endswith("/missing") else "up",
            checked_at=datetime.now(timezone.utc),
            status_code=404 if url.endswith("/missing") else 200,
        )

    monkeypatch.setattr(engine, "scan_url", fake_scan)
    html = """
    <html><head><title>First page</title>
    <meta name="description" content="Demo description">
    <meta name="robots" content="noindex, follow">
    <link rel="canonical" href="/canonical">
    <script src="https://www.googletagmanager.com/gtag/js?id=G-ABCD1234"></script>
    <script>fbq('init', '123456789'); const gtm = 'GTM-ABC123';</script>
    </head><body><h1>Hello</h1>
    <a href="/okay">Valid</a><a href="/missing">Broken</a>
    <a href="/okay">Duplicate</a>
    <a href="https://another.example.com/outside">External</a>
    <a href="http://127.0.0.1/private">Internal</a>
    <a href="mailto:hello@example.com">Email</a>
    </body></html>
    """
    result = engine.analyze_html(html, "https://example.com/", link_limit=2)
    assert result["seo"]["title"] == "First page"
    assert result["seo"]["canonical"] == "https://example.com/canonical"
    assert result["seo"]["issues"] == ["noindex"]
    assert result["tracking"]["ga4"]["ids"] == ["G-ABCD1234"]
    assert result["tracking"]["gtm"]["ids"] == ["GTM-ABC123"]
    assert result["tracking"]["meta_pixel"]["ids"] == ["123456789"]
    assert result["links"]["checked"] == 2
    assert len(result["links"]["broken"]) == 1
    assert result["links"]["broken"][0]["status_code"] == 404
    assert len(visited) == 2
    assert all(url.startswith("https://example.com/") for url in visited)
    assert result["links"]["skipped_external"] == 1
    assert result["links"]["skipped_unsupported"] == 2


def test_link_budget_and_seo_missing_values(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "scan_url", lambda url: calls.append(url) or ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200
    ))
    html = "".join(f'<a href="/page/{i}">Page</a>' for i in range(30))
    result = engine.analyze_html(html, "https://example.com/", link_limit=3)
    assert len(calls) == 3
    assert result["links"]["candidates"] == 30
    assert result["links"]["truncated"] is True
    assert set(result["seo"]["issues"]) == {"missing_title", "missing_meta_description", "missing_h1"}
    with pytest.raises(ValueError):
        engine.analyze_html(html, "https://example.com/", link_limit=100)
    with pytest.raises(ValueError):
        engine.analyze_html(html, "https://example.com/", link_limit=True)


def test_redirects_revalidated_and_bounded(monkeypatch):
    calls = []

    def redirect_once(url):
        calls.append(url)
        return 302, "http://127.0.0.1/admin", "text/html", b""

    monkeypatch.setattr(engine, "_fetch_once", redirect_once)
    with pytest.raises(UnsafeTarget):
        engine.fetch_page("https://example.com/")
    assert calls == ["https://example.com/"]


def test_page_failures_are_reported_without_fake_seo(monkeypatch):
    monkeypatch.setattr(engine, "fetch_page", lambda url: engine.PageResponse(
        503, url, "text/html", b""
    ))
    result = engine.audit_site("https://example.com/")
    assert result["status"] == "failed"
    assert result["error"] == "HTTP 503"
    assert result["findings"] == {}


def test_personal_audit_persist_and_access(client, tokens, monkeypatch):
    monitor = make_monitor(client, tokens)
    monkeypatch.setattr("sitesentinel.api.audits.audit_site", fake_audit)
    headers = auth_header(tokens["access_token"])
    endpoint = f"{ROOT}/{monitor['id']}/audits"

    created = client.post(endpoint, json={"link_limit": 2}, headers=headers)
    assert created.status_code == 201, created.json
    data = created.json["audit"]
    assert data["findings"]["tracking"]["ga4"]["detected"] is True
    assert data["link_limit"] == 2
    assert db.session.get(SiteAudit, data["id"]) is not None

    listing = client.get(endpoint, headers=headers)
    assert listing.status_code == 200
    assert len(listing.json["audits"]) == 1
    assert "findings" not in listing.json["audits"][0]
    detail = client.get(f"{endpoint}/{data['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json["audit"]["findings"] == data["findings"]
    assert client.get(endpoint + "?limit=0", headers=headers).status_code == 400
    assert client.get(f"{endpoint}/not-a-report", headers=headers).status_code == 404


def test_audit_request_validation_and_disabled_monitor(client, tokens, monkeypatch):
    monitor = make_monitor(client, tokens)
    headers = auth_header(tokens["access_token"])
    url = f"{ROOT}/{monitor['id']}/audits"
    for value in (-1, 11, True, "5", None):
        response = client.post(url, headers=headers, json={"link_limit": value})
        assert response.status_code == 400
    assert client.post(url, headers=headers, json={"url": "https://other.com"}).status_code == 400
    assert client.post(url, headers=headers, json=["invalid"]).status_code == 400
    assert client.post(url).status_code == 401

    monkeypatch.setattr("sitesentinel.api.audits.audit_site", lambda *_args, **_kwargs: {
        "status": "failed", "final_url": None, "status_code": None,
        "findings": {}, "error": "DNS lookup failed.",
    })
    response = client.post(url, headers=headers)
    assert response.status_code == 201
    assert response.json["audit"]["status"] == "failed"
    assert response.json["audit"]["error"] == "DNS lookup failed."

    client.patch(f"{ROOT}/{monitor['id']}", headers=headers, json={"is_enabled": False})
    assert client.post(url, headers=headers).status_code == 409


def test_workspace_permissions_and_tenant_isolation(client, tokens, monkeypatch):
    owner_headers = auth_header(tokens["access_token"])
    team = client.post("/api/v1/workspaces", headers=owner_headers, json={"name": "Audit Team"})
    assert team.status_code == 201
    ws = team.json["workspace"]["id"]

    created = client.post(
        f"/api/v1/workspaces/{ws}/monitors", headers=owner_headers,
        json={"name": "Team site", "url": "https://example.org/"},
    )
    assert created.status_code == 201, created.json
    monitor_id = created.json["monitor"]["id"]
    endpoint = f"/api/v1/workspaces/{ws}/monitors/{monitor_id}/audits"
    monkeypatch.setattr("sitesentinel.api.audits.audit_site", fake_audit)

    first = client.post(endpoint, headers=owner_headers, json={"link_limit": 0})
    assert first.status_code == 201, first.json

    register = client.post("/api/v1/auth/register", json={
        "email": "viewer@example.com", "display_name": "Viewer",
        "password": "long-demo-password-123",
    })
    assert register.status_code == 201
    viewer = db.session.scalar(db.select(User).where(User.email == "viewer@example.com"))
    db.session.add(Membership(workspace_id=ws, user_id=viewer.id, role="viewer"))
    db.session.commit()
    login = client.post("/api/v1/auth/login", json={
        "email": "viewer@example.com", "password": "long-demo-password-123",
    })
    viewer_headers = auth_header(login.json["access_token"])

    assert client.get(endpoint, headers=viewer_headers).status_code == 200
    assert client.get(f"{endpoint}/{first.json['audit']['id']}", headers=viewer_headers).status_code == 200
    assert client.post(endpoint, headers=viewer_headers).status_code == 403
    # The legacy personal routes never expose a team's audit data.
    assert client.get(f"{ROOT}/{monitor_id}/audits", headers=owner_headers).status_code == 404
    assert client.get(f"{ROOT}/{monitor_id}/audits", headers=viewer_headers).status_code == 404
    db.session.delete(db.session.scalar(
        db.select(Membership).where(Membership.workspace_id == ws, Membership.user_id == viewer.id)
    ))
    db.session.commit()
    assert client.get(endpoint, headers=viewer_headers).status_code == 404


def test_deleting_monitor_cascades_to_audits(client, tokens, monkeypatch):
    monitor = make_monitor(client, tokens)
    monkeypatch.setattr("sitesentinel.api.audits.audit_site", fake_audit)
    headers = auth_header(tokens["access_token"])
    endpoint = f"{ROOT}/{monitor['id']}/audits"
    audit_id = client.post(endpoint, headers=headers, json={"link_limit": 0}).json["audit"]["id"]
    assert client.delete(f"{ROOT}/{monitor['id']}", headers=headers).status_code == 204
    assert db.session.get(SiteAudit, audit_id) is None
