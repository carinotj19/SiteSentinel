"""Read-only, loopback-only portfolio showcase with synthetic data.

Run: python -m sitesentinel.portfolio_demo

Unlike normal development, this entrypoint never opens the configured database,
never schedules network scans, and never sends email. It creates an isolated
in-memory SQLite database and exposes sample results through the *real* read APIs.
"""

import argparse
import secrets
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

from sitesentinel import create_app
from sitesentinel.extensions import db
from sitesentinel.models import Membership, Monitor, SiteAudit, User, Workspace
from sitesentinel.monitoring.scanner import ScanResult
from sitesentinel.monitoring.service import record_scan

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "portfolio-demo-only-2026"


def _create_workspace(user: User, name: str, personal: bool) -> Workspace:
    workspace = Workspace(
        name=name,
        created_by_user_id=user.id,
        personal_owner_user_id=user.id if personal else None,
    )
    db.session.add(workspace)
    db.session.flush()
    db.session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    return workspace


def _create_monitor(user: User, workspace: Workspace, name: str, url: str) -> Monitor:
    monitor = Monitor(
        user_id=user.id, workspace_id=workspace.id,
        name=name, url=url, is_enabled=True, interval_seconds=300,
    )
    db.session.add(monitor)
    db.session.flush()
    return monitor


def _record_history(monitor: Monitor, now: datetime, statuses: list[str], latencies: list[int]):
    """Use the actual incident state machine, never HTTP requests."""
    for index, (status, latency) in enumerate(zip(statuses, latencies, strict=True)):
        checked_at = now - timedelta(minutes=(len(statuses) - index) * 35)
        record_scan(monitor, ScanResult(
            status=status,
            checked_at=checked_at,
            status_code=200 if status == "up" else 503,
            response_time_ms=latency if status == "up" else None,
            final_url=monitor.url,
            error=None if status == "up" else "HTTP 503 (synthetic sample)",
        ))


def _add_audit(
    monitor: Monitor, when: datetime, *,
    issues: list[str], broken: bool, tracking: bool, failed: bool = False,
) -> None:
    link = f"{monitor.url.rstrip('/')}/missing"
    findings = {} if failed else {
        "seo": {
            "title": "Demo page" if "missing_title" not in issues else None,
            "meta_description": "Synthetic example metadata.",
            "h1": ["Demo page"],
            "canonical": monitor.url,
            "robots": "noindex" if "noindex" in issues else "index, follow",
            "issues": issues,
        },
        "tracking": {
            "ga4": {"detected": tracking, "ids": ["G-DEMO123"] if tracking else []},
            "gtm": {"detected": False, "ids": []},
            "meta_pixel": {"detected": False, "ids": []},
            "note": "Sample data; static evidence does not prove analytics delivery.",
        },
        "links": {
            "candidates": 2,
            "checked": 2,
            "broken": [{
                "url": link, "status": "down", "status_code": 404, "error": "HTTP 404",
            }] if broken else [],
            "results": [
                {"url": monitor.url, "status": "up", "status_code": 200, "error": None},
                {
                    "url": link,
                    "status": "down" if broken else "up",
                    "status_code": 404 if broken else 200,
                    "error": "HTTP 404" if broken else None,
                },
            ],
            "skipped_external": 1,
            "skipped_unsupported": 0,
            "truncated": False,
        },
    }
    db.session.add(SiteAudit(
        monitor_id=monitor.id,
        audited_at=when,
        requested_url=monitor.url,
        final_url=None if failed else monitor.url,
        status="failed" if failed else "completed",
        status_code=503 if failed else 200,
        link_limit=2,
        findings=findings,
        error="HTTP 503 (synthetic sample)" if failed else None,
    ))


def _seed_sample_data() -> None:
    user = User(email=DEMO_EMAIL, display_name="Portfolio Demo")
    user.set_password(DEMO_PASSWORD)
    db.session.add(user)
    db.session.flush()

    personal = _create_workspace(user, "Personal — Sample Websites", personal=True)
    team = _create_workspace(user, "Demo Agency Workspace", personal=False)
    storefront = _create_monitor(
        user, personal, "[Demo] Storefront — healthy", "https://example.com/storefront"
    )
    checkout = _create_monitor(
        user, personal, "[Demo] Checkout — outage", "https://example.org/checkout"
    )
    blog = _create_monitor(
        user, team, "[Demo] Blog — recovered", "https://example.net/blog"
    )

    now = datetime.now(timezone.utc)
    _record_history(storefront, now, ["up"] * 6, [128, 142, 115, 133, 121, 125])
    _record_history(checkout, now, ["up", "up", "down", "down"], [210, 240, 0, 0])
    _record_history(blog, now, ["up", "down", "down", "up"], [180, 0, 0, 162])
    _add_audit(storefront, now - timedelta(minutes=8), issues=[], broken=False, tracking=True)
    _add_audit(
        checkout, now - timedelta(minutes=12),
        issues=["missing_title", "noindex"], broken=True, tracking=False,
    )
    _add_audit(blog, now - timedelta(minutes=5), issues=[], broken=False, tracking=False)
    _add_audit(
        blog, now - timedelta(minutes=17), issues=[], broken=False,
        tracking=False, failed=True,
    )
    db.session.commit()


def build_demo_app():
    """Create a standalone showcase; never reuse the user's local DB or keys."""
    app = create_app({
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "JWT_SECRET_KEY": secrets.token_urlsafe(48),
        "SMTP_HOST": "",
        "TESTING": False,
    })
    with app.app_context():
        db.create_all()  # Safe *only* for the ephemeral showcase, not migrations.
        _seed_sample_data()

    @app.before_request
    def demo_read_only():
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        # Allow authentication in Swagger, but never registration, scans,
        # workspace edits, invitations, or other writes in demo mode.
        if request.method == "POST" and request.path in {
            "/api/v1/auth/login", "/api/v1/auth/refresh",
        }:
            return None
        return jsonify({
            "error": {
                "code": "demo_read_only",
                "message": "This local portfolio showcase is read-only. Use the normal app for writes.",
            }
        }), 403

    return app


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Launch a synthetic, read-only SiteSentinel portfolio demo on localhost."
    )
    parser.add_argument("--port", type=int, default=8000, help="Local port (default: 8000).")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be in the range 1–65535")

    app = build_demo_app()
    print("\n=== SiteSentinel read-only portfolio showcase ===", flush=True)
    print("Synthetic data only; nothing is fetched or emailed.", flush=True)
    print("Database: isolated SQLite in memory (deleted on exit).", flush=True)
    print(f"Swagger: http://127.0.0.1:{args.port}/api/docs", flush=True)
    print(f"Login email: {DEMO_EMAIL}", flush=True)
    print(f"Login password: {DEMO_PASSWORD}", flush=True)
    print("Only listening on 127.0.0.1. Press Ctrl+C to stop.\n", flush=True)
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False, threaded=False)


if __name__ == "__main__":
    main()
