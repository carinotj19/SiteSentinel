"""Isolated SQLite database per test, without a running PostgreSQL container."""

import pytest

from sitesentinel import create_app
from sitesentinel.extensions import db


@pytest.fixture()
def app():
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "test-only-signing-secret-never-deploy",
    })
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def registered(client):
    payload = {
        "email": "alice@example.com",
        "display_name": "Alice Example",
        "password": "a-strong-test-password-123",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    return payload


@pytest.fixture()
def tokens(client, registered):
    response = client.post("/api/v1/auth/login", json={
        "email": registered["email"], "password": registered["password"]
    })
    assert response.status_code == 200
    return response.json


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}
