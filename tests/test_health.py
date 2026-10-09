"""Health checks and JSON HTTP errors."""

from sitesentinel import create_app


def test_liveness_and_readiness(client):
    assert client.get("/api/v1/health/live").json == {"service": "sitesentinel", "status": "ok"}
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json == {"status": "ready", "database": "ok"}


def test_404_and_security_headers(client):
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json["error"]["code"] == "http_error"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_signing_key_is_required():
    import pytest
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        create_app({"JWT_SECRET_KEY": None, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
