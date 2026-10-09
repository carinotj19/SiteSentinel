"""Core authentication and account security expectations."""

from werkzeug.security import check_password_hash

from sitesentinel.extensions import db
from sitesentinel.models import User
from tests.conftest import auth_header


def test_register_returns_safe_user(client):
    response = client.post("/api/v1/auth/register", json={
        "email": " ALICE@EXAMPLE.COM ",
        "display_name": "  Alice  ",
        "password": "a-strong-test-password-123",
    })
    assert response.status_code == 201
    user = response.json["user"]
    assert user["email"] == "alice@example.com"
    assert user["display_name"] == "Alice"
    assert "password" not in str(response.json)
    stored = db.session.get(User, user["id"])
    assert stored.password_hash != "a-strong-test-password-123"
    assert check_password_hash(stored.password_hash, "a-strong-test-password-123")


def test_duplicate_registration_is_conflict(client, registered):
    response = client.post("/api/v1/auth/register", json={**registered, "email": "ALICE@example.com"})
    assert response.status_code == 409
    assert response.json["error"]["code"] == "email_taken"


def test_registration_validates_json_and_fields(client):
    url = "/api/v1/auth/register"
    assert client.post(url, data="hello").status_code == 400
    assert client.post(url, json=[]).status_code == 400
    assert client.post(url, json={"email": "bad", "display_name": "Al", "password": "supersecretpassword"}).status_code == 400
    assert client.post(url, json={"email": "a@example.com", "display_name": "Al", "password": "short"}).status_code == 400
    assert client.post(url, json={"email": "a@example.com", "display_name": "A", "password": "longenoughpassword"}).status_code == 400


def test_login_success_and_generic_failure(client, registered):
    bad = client.post("/api/v1/auth/login", json={"email": registered["email"], "password": "incorrect"})
    missing = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "incorrect"})
    assert bad.status_code == missing.status_code == 401
    assert bad.json == missing.json
    good = client.post("/api/v1/auth/login", json={"email": "ALICE@example.com", "password": registered["password"]})
    assert good.status_code == 200
    assert good.json["access_token"] and good.json["refresh_token"]
    assert good.json["token_type"] == "Bearer"


def test_me_requires_auth_and_cannot_mass_assign(client, tokens):
    url = "/api/v1/users/me"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=auth_header("definitely-invalid")).status_code == 422
    authorized = auth_header(tokens["access_token"])
    assert client.get(url, headers=authorized).status_code == 200
    rejected = client.patch(url, headers=authorized, json={"is_active": False, "auth_version": 25})
    assert rejected.status_code == 400
    updated = client.patch(url, headers=authorized, json={"display_name": "  Alice Updated  "})
    assert updated.status_code == 200
    assert updated.json["user"]["display_name"] == "Alice Updated"


def test_refresh_single_use_and_access_token_type(client, tokens):
    url = "/api/v1/auth/refresh"
    assert client.post(url, headers=auth_header(tokens["access_token"])).status_code != 200
    first = client.post(url, headers=auth_header(tokens["refresh_token"]))
    assert first.status_code == 200
    assert first.json["access_token"]
    assert first.json["refresh_token"] != tokens["refresh_token"]
    repeat = client.post(url, headers=auth_header(tokens["refresh_token"]))
    assert repeat.status_code == 401
    assert repeat.json["error"]["code"] == "token_revoked"
    assert client.post(url, headers=auth_header(first.json["refresh_token"])).status_code == 200


def test_logout_revokes_all_access_and_refresh_tokens(client, tokens):
    login_again = client.post("/api/v1/auth/login", json={
        "email": "alice@example.com", "password": "a-strong-test-password-123"
    }).json
    response = client.delete("/api/v1/auth/logout", headers=auth_header(tokens["access_token"]))
    assert response.status_code == 204
    assert client.get("/api/v1/users/me", headers=auth_header(tokens["access_token"])).status_code == 401
    assert client.post("/api/v1/auth/refresh", headers=auth_header(tokens["refresh_token"])).status_code == 401
    assert client.get("/api/v1/users/me", headers=auth_header(login_again["access_token"])).status_code == 401
    fresh_login = client.post("/api/v1/auth/login", json={
        "email": "alice@example.com", "password": "a-strong-test-password-123"
    })
    assert fresh_login.status_code == 200


def test_inactive_user_token_rejected(client, tokens):
    user = db.session.scalar(db.select(User).filter_by(email="alice@example.com"))
    user.is_active = False
    db.session.commit()
    assert client.get("/api/v1/users/me", headers=auth_header(tokens["access_token"])).status_code == 401
