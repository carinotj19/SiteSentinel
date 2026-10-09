"""Role-gated workspace actions, invitation replay, and strict tenant isolation."""

from datetime import datetime, timedelta, timezone
from sitesentinel.extensions import db
from sitesentinel.models import Invitation
from sitesentinel.monitoring.scanner import ScanResult
from tests.conftest import auth_header

ROOT = "/api/v1/workspaces"


def another_user(client, email="second@example.com"):
    password = "a-long-password-123-xyz"
    response = client.post("/api/v1/auth/register", json={
        "email": email, "display_name": "Other Person", "password": password,
    })
    assert response.status_code == 201, response.json
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return auth_header(response.json["access_token"])


def create_workspace(client, headers, name="Acme Team"):
    res = client.post(ROOT, headers=headers, json={"name": name})
    assert res.status_code == 201, res.json
    return res.json["workspace"]


def invite_and_accept(client, owner, invited, workspace, role="viewer", email="second@example.com"):
    url = f"{ROOT}/{workspace['id']}/invitations"
    response = client.post(url, headers=owner, json={"email": email, "role": role})
    assert response.status_code == 201, response.json
    token = response.json["token"]
    accepted = client.post(f"{ROOT}/invitations/accept", headers=invited, json={"token": token})
    assert accepted.status_code == 200, accepted.json
    return token


def test_registration_creates_personal_workspace(client, tokens):
    headers = auth_header(tokens["access_token"])
    response = client.get(ROOT, headers=headers)
    assert response.status_code == 200
    personal = response.json["workspaces"]
    assert len(personal) == 1
    assert personal[0]["role"] == "owner"
    assert personal[0]["is_personal"] is True
    monitor = client.post("/api/v1/monitors", headers=headers,
                          json={"name": "Existing API", "url": "https://example.com"})
    assert monitor.status_code == 201
    assert monitor.json["monitor"]["workspace_id"] == personal[0]["id"]
    url = f"{ROOT}/{personal[0]['id']}/monitors"
    assert client.get(url, headers=headers).json["total"] == 1
    assert client.get("/api/v1/monitors", headers=headers).json["total"] == 1


def test_workspace_creation_and_access_control(client, tokens):
    headers = auth_header(tokens["access_token"])
    other = another_user(client)
    workspace = create_workspace(client, headers)
    wid = workspace["id"]
    assert client.get(f"{ROOT}/{wid}", headers=other).status_code == 404
    assert client.get(f"{ROOT}/{wid}/members", headers=other).status_code == 404
    assert client.get(f"{ROOT}/{wid}/monitors", headers=other).status_code == 404
    assert client.post(f"{ROOT}/{wid}/monitors", headers=other, json={"name": "No", "url": "https://example.com"}).status_code == 404
    assert client.patch(f"{ROOT}/{wid}", headers=other, json={"name": "Hacked"}).status_code == 404
    changed = client.patch(f"{ROOT}/{wid}", headers=headers, json={"name": "Renamed Team"})
    assert changed.status_code == 200
    assert changed.json["workspace"]["name"] == "Renamed Team"
    assert client.post(ROOT, headers=headers, json={"name": " "}).status_code == 400
    assert client.post(ROOT, headers=headers, json={"name": "Team", "role": "owner"}).status_code == 400


def test_viewer_read_only_and_member_writes(client, tokens, monkeypatch):
    owner = auth_header(tokens["access_token"])
    invited = another_user(client)
    workspace = create_workspace(client, owner)
    wid = workspace["id"]
    monitor_url = f"{ROOT}/{wid}/monitors"
    created = client.post(monitor_url, headers=owner, json={"name": "Project", "url": "https://example.com"})
    assert created.status_code == 201, created.json
    mid = created.json["monitor"]["id"]
    invite_and_accept(client, owner, invited, workspace)
    assert client.get(monitor_url, headers=invited).json["total"] == 1
    assert client.get(f"{monitor_url}/{mid}", headers=invited).status_code == 200
    for method, route, body in [
        ("post", monitor_url, {"name": "Blocked", "url": "https://example.org"}),
        ("patch", f"{monitor_url}/{mid}", {"name": "Changed"}),
        ("delete", f"{monitor_url}/{mid}", None),
        ("post", f"{monitor_url}/{mid}/check", None),
    ]:
        kw = {"json": body} if body else {}
        assert getattr(client, method)(route, headers=invited, **kw).status_code == 403
    assert client.post(f"{ROOT}/{wid}/invitations", headers=invited,
                       json={"email": "third@example.com", "role": "member"}).status_code == 403
    new_role = client.patch(f"{ROOT}/{wid}/members/{client.get(f'{ROOT}/{wid}/members', headers=owner).json['members'][1]['user_id']}",
                            headers=owner, json={"role": "member"})
    assert new_role.status_code == 200, new_role.json
    assert new_role.json["member"]["role"] == "member"
    assert client.patch(f"{monitor_url}/{mid}", headers=invited, json={"name": "Member edit"}).status_code == 200
    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda url: ScanResult(
        status="up", checked_at=datetime.now(timezone.utc), status_code=200, final_url=url
    ))
    assert client.post(f"{monitor_url}/{mid}/check", headers=invited).status_code == 201
    assert len(client.get(f"{monitor_url}/{mid}/checks", headers=owner).json["checks"]) == 1


def test_invitation_wrong_user_replay_revoke_and_expire(client, tokens):
    owner = auth_header(tokens["access_token"])
    other = another_user(client)
    third = another_user(client, "third@example.com")
    workspace = create_workspace(client, owner)
    route = f"{ROOT}/{workspace['id']}/invitations"
    response = client.post(route, headers=owner, json={"email": "second@example.com", "role": "viewer"})
    assert response.status_code == 201
    token = response.json["token"]
    assert len(token) >= 32
    invitation = db.session.get(Invitation, response.json["invitation"]["id"])
    assert invitation.token_hash != token
    assert client.post(f"{ROOT}/invitations/accept", headers=third, json={"token": token}).status_code == 403
    assert client.post(f"{ROOT}/invitations/accept", headers=other, json={"token": token}).status_code == 200
    assert client.post(f"{ROOT}/invitations/accept", headers=other, json={"token": token}).status_code == 404
    assert client.post(route, headers=owner, json={"email": "second@example.com", "role": "viewer"}).status_code == 409
    response = client.post(route, headers=owner, json={"email": "third@example.com", "role": "member"})
    assert response.status_code == 201
    token = response.json["token"]
    assert client.delete(f"{route}/{response.json['invitation']['id']}", headers=owner).status_code == 204
    assert client.post(f"{ROOT}/invitations/accept", headers=third, json={"token": token}).status_code == 404
    response = client.post(route, headers=owner, json={"email": "third@example.com", "role": "member"})
    invitation = db.session.get(Invitation, response.json["invitation"]["id"])
    invitation.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.session.commit()
    assert client.post(f"{ROOT}/invitations/accept", headers=third, json={"token": response.json['token']}).status_code == 410


def test_owner_protection_and_admin_invites(client, tokens):
    owner = auth_header(tokens["access_token"])
    admin = another_user(client)
    workspace = create_workspace(client, owner)
    invite_and_accept(client, owner, admin, workspace, role="admin")
    wid = workspace["id"]
    assert client.patch(f"{ROOT}/{wid}/members/{tokens['user']['id']}", headers=owner, json={"role": "viewer"}).status_code == 409
    assert client.delete(f"{ROOT}/{wid}/members/{tokens['user']['id']}", headers=owner).status_code == 409
    assert client.post(f"{ROOT}/{wid}/invitations", headers=admin,
                       json={"email": "third@example.com", "role": "admin"}).status_code == 400
    allowed = client.post(f"{ROOT}/{wid}/invitations", headers=admin,
                          json={"email": "third@example.com", "role": "viewer"})
    assert allowed.status_code == 201
    assert client.patch(f"{ROOT}/{wid}/members/{tokens['user']['id']}", headers=admin,
                        json={"role": "member"}).status_code == 403
    assert client.delete(f"{ROOT}/{wid}/members/{tokens['user']['id']}", headers=admin).status_code == 403


def test_tenant_records_not_visible_between_workspaces(client, tokens, monkeypatch):
    owner = auth_header(tokens["access_token"])
    other = another_user(client)
    first = create_workspace(client, owner, "First Workspace")
    second = create_workspace(client, owner, "Second Workspace")
    url = "https://example.com"
    first_route = f"{ROOT}/{first['id']}/monitors"
    second_route = f"{ROOT}/{second['id']}/monitors"
    a = client.post(first_route, headers=owner, json={"name": "Site A", "url": url})
    b = client.post(second_route, headers=owner, json={"name": "Site B", "url": url})
    assert a.status_code == b.status_code == 201
    a_id = a.json["monitor"]["id"]
    b_id = b.json["monitor"]["id"]
    assert client.get(f"{second_route}/{a_id}", headers=owner).status_code == 404
    assert client.get(f"{first_route}/{b_id}", headers=owner).status_code == 404
    assert client.get("/api/v1/monitors", headers=owner).json["total"] == 0
    assert client.get(f"{first_route}/{a_id}/checks", headers=other).status_code == 404
    monkeypatch.setattr("sitesentinel.api.monitors.scan_url", lambda url: ScanResult(
        status="down", checked_at=datetime.now(timezone.utc), status_code=503, final_url=url,
        error="HTTP 503"
    ))
    for _ in range(2):
        assert client.post(f"{first_route}/{a_id}/check", headers=owner).status_code == 201
    assert client.get(f"{first_route}/{a_id}/incidents", headers=owner).json["incidents"][0]["status"] == "open"
    for endpoint in ("checks", "incidents", "alerts"):
        assert client.get(f"{second_route}/{a_id}/{endpoint}", headers=owner).status_code == 404
        assert client.get(f"{first_route}/{a_id}/{endpoint}", headers=other).status_code == 404


def test_unknown_workspace_payload_validation(client, tokens):
    owner = auth_header(tokens["access_token"])
    ws = create_workspace(client, owner)
    wid = ws["id"]
    assert client.post(f"{ROOT}/{wid}/invitations", headers=owner, json={"email": "third@example.com", "role": {"oops": 1}}).status_code == 400
    assert client.post(f"{ROOT}/{wid}/invitations", headers=owner, json={"email": "third@example.com", "role": "owner"}).status_code == 400
    assert client.post(f"{ROOT}/invitations/accept", headers=owner, json={"token": "bad"}).status_code == 404
    assert client.get(f"{ROOT}/{wid}/invitations", headers=owner).status_code == 200
    assert client.get(ROOT).status_code == 401
