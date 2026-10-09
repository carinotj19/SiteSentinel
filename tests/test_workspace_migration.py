"""Existing SQLite users, monitors and historical checks survive workspace migration."""

from datetime import datetime, timezone
from uuid import uuid4

from flask_migrate import upgrade
from sqlalchemy import text

from sitesentinel import create_app
from sitesentinel.extensions import db


def test_migration_backfills_personal_workspaces_without_losing_history(tmp_path):
    database_path = tmp_path / "legacy.db"
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        "JWT_SECRET_KEY": "migration-test-only-secret",
    })
    with app.app_context():
        upgrade(revision="0003_background_jobs")
        now = datetime.now(timezone.utc)
        users = [str(uuid4()), str(uuid4())]
        monitors = [str(uuid4()), str(uuid4())]
        check_ids = [str(uuid4()), str(uuid4())]
        for n, user_id in enumerate(users):
            db.session.execute(text(
                "INSERT INTO users (id,email,display_name,password_hash,is_active,auth_version,created_at,updated_at) "
                "VALUES (:id,:email,:name,:hash,1,0,:now,:now)"
            ), {"id": user_id, "email": f"old{n}@example.com", "name": f"Owner {n}",
                "hash": "legacy-hash", "now": now})
            db.session.execute(text(
                "INSERT INTO monitors (id,user_id,name,url,is_enabled,last_status,created_at,updated_at) "
                "VALUES (:id,:user_id,:name,:url,1,'up',:now,:now)"
            ), {"id": monitors[n], "user_id": user_id, "name": f"Legacy site {n}",
                "url": "https://example.com/", "now": now})
            db.session.execute(text(
                "INSERT INTO check_results (id,monitor_id,checked_at,checked_url,status,status_code,response_time_ms) "
                "VALUES (:id,:monitor_id,:now,'https://example.com/','up',200,150)"
            ), {"id": check_ids[n], "monitor_id": monitors[n], "now": now})
        db.session.commit()

        upgrade(revision="head")
        assert db.session.scalar(text("SELECT COUNT(*) FROM workspaces")) == 2
        assert db.session.scalar(text("SELECT COUNT(*) FROM workspace_memberships")) == 2
        assert db.session.scalar(text("SELECT COUNT(*) FROM monitors WHERE workspace_id IS NOT NULL")) == 2
        assert db.session.scalar(text("SELECT COUNT(*) FROM check_results")) == 2
        assert set(db.session.execute(text("SELECT id FROM check_results")).scalars()) == set(check_ids)
        rows = db.session.execute(text(
            "SELECT m.id, w.personal_owner_user_id FROM monitors AS m "
            "JOIN workspaces AS w ON m.workspace_id = w.id"
        )).all()
        assert set(rows) == set(zip(monitors, users))
        assert set(db.session.execute(text("SELECT role FROM workspace_memberships")).scalars()) == {"owner"}