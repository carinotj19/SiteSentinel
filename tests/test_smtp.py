"""SMTP messages use the configured sender, recipient and TLS without real network traffic."""

from datetime import datetime, timezone

import smtplib

from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, Incident, Monitor
from sitesentinel.monitoring.worker import _send_email
from tests.conftest import auth_header


class FakeSMTP:
    def __init__(self, host, port, timeout):
        assert (host, port, timeout) == ("smtp.gmail.com", 587, 10)
        self.calls = []
        self.message = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self, context):
        assert context is not None
        self.calls.append("tls")

    def login(self, username, password):
        assert (username, password) == ("test@gmail.com", "not-a-real-app-password")
        self.calls.append("login")

    def send_message(self, message):
        self.calls.append("send")
        self.message = message


def test_smtp_down_and_recovered_email_payload(app, client, tokens, monkeypatch):
    headers = auth_header(tokens["access_token"])
    m = client.post("/api/v1/monitors", headers=headers, json={
        "name": "Example", "url": "https://example.com"
    }).json["monitor"]
    monitor = db.session.get(Monitor, m["id"])
    incident = Incident(monitor_id=monitor.id, opened_at=datetime.now(timezone.utc), reason="HTTP 503")
    db.session.add(incident)
    db.session.flush()
    event = AlertEvent(monitor_id=monitor.id, incident_id=incident.id, kind="down")
    db.session.add(event)
    db.session.commit()

    app.config.update({
        "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": 587,
        "SMTP_FROM": "test@gmail.com", "SMTP_USERNAME": "test@gmail.com",
        "SMTP_PASSWORD": "not-a-real-app-password", "SMTP_STARTTLS": True,
    })
    connections = []

    def fake_connection(host, port, timeout):
        connection = FakeSMTP(host, port, timeout)
        connections.append(connection)
        return connection

    monkeypatch.setattr(smtplib, "SMTP", fake_connection)
    for kind, subject in [("down", "website down"), ("recovered", "website recovered")]:
        event.kind = kind
        _send_email(event)
        connection = connections[-1]
        assert connection.calls == ["tls", "login", "send"]
        assert connection.message["Subject"] == f"SiteSentinel: {subject}"
        assert connection.message["To"] == "alice@example.com"
        assert connection.message["From"] == "test@gmail.com"
        assert "https://example.com/" in connection.message.get_content()
