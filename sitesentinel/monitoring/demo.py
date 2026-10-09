"""Deterministic, offline incident lifecycle demonstration.

Run from the repository root with:
    python -m sitesentinel.monitoring.demo

Uses a separate, in-memory SQLite database. Never calls the network scanner,
sends email, or changes the user's real monitors and incident history.
"""

from datetime import datetime, timedelta, timezone

from sitesentinel import create_app
from sitesentinel.extensions import db
from sitesentinel.models import AlertEvent, CheckResult, Incident, Monitor, User
from sitesentinel.monitoring.scanner import ScanResult
from sitesentinel.monitoring.service import record_scan


def run_demo() -> None:
    """Prove one outage and one recovery notification across five fake scans."""
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": "isolated-demo-key-not-for-production",
        "SMTP_HOST": "",  # Never send emails from this demonstration.
    })
    with app.app_context():
        db.create_all()
        user = User(email="demo@example.com", display_name="Demo User")
        user.set_password("test-only-demo-password-123")
        db.session.add(user)
        db.session.flush()

        monitor = Monitor(
            user_id=user.id,
            name="Outage demo (simulated)",
            url="https://example.com/",
            is_enabled=True,
            interval_seconds=60,
            failure_streak=0,
        )
        db.session.add(monitor)
        db.session.commit()

        print("Isolated demo: no network requests, no emails, no changes to your SQLite database.")
        print("Step                 HTTP  Open incidents  Alert events")
        print("-------------------  ----  --------------  ------------------")
        start = datetime.now(timezone.utc)
        sequence = [
            ("Healthy", "up", 200),
            ("First failure", "down", 503),
            ("Second failure", "down", 503),
            ("Third failure", "down", 503),
            ("Recovered", "up", 200),
        ]
        expected_open = [0, 0, 1, 1, 0]
        expected_kinds = [[], [], ["down"], ["down"], ["down", "recovered"]]

        for index, (label, status, code) in enumerate(sequence):
            record_scan(monitor, ScanResult(
                status=status,
                checked_at=start + timedelta(minutes=index),
                status_code=code,
                response_time_ms=125,
                final_url=monitor.url,
                error=f"HTTP {code}" if status == "down" else None,
            ))
            db.session.commit()
            open_count = db.session.scalar(
                db.select(db.func.count()).select_from(Incident).where(
                    Incident.monitor_id == monitor.id, Incident.resolved_at.is_(None)
                )
            )
            events = db.session.scalars(
                db.select(AlertEvent).where(AlertEvent.monitor_id == monitor.id)
                .order_by(AlertEvent.created_at, AlertEvent.id)
            ).all()
            kinds = sorted(event.kind for event in events)
            assert open_count == expected_open[index], f"Incorrect incident state at {label}"
            assert kinds == sorted(expected_kinds[index]), f"Incorrect alert events at {label}"
            assert all(event.delivery_status == "pending" for event in events)
            print(f"{label:<19}  {code:<4}  {open_count:<14}  {', '.join(kinds) or 'none'}")

        incidents = db.session.scalars(db.select(Incident)).all()
        checks = db.session.scalar(db.select(db.func.count()).select_from(CheckResult))
        assert checks == 5
        assert len(incidents) == 1 and incidents[0].resolved_at is not None
        assert monitor.failure_streak == 0
        print("\nPASS: 5 checks, 1 resolved incident, 1 down event, 1 recovery event.")
        db.session.remove()
        db.drop_all()


if __name__ == "__main__":
    run_demo()
