"""SiteSentinel Flask application factory."""

from flask import Flask, request
from werkzeug.exceptions import HTTPException

from sitesentinel.config import Config
from sitesentinel.extensions import db, jwt, migrate


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    if not app.config.get("JWT_SECRET_KEY"):
        raise RuntimeError("JWT_SECRET_KEY must be configured before starting SiteSentinel")

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    # Import models before Alembic inspects SQLAlchemy metadata.
    from sitesentinel.api.auth import bp as auth_bp
    from sitesentinel.api.health import bp as health_bp
    from sitesentinel.api.users import bp as users_bp
    from sitesentinel.models import ConsumedRefreshToken, User

    app.register_blueprint(auth_bp)
    app.register_blueprint(health_bp)
    from sitesentinel.api.monitors import bp as monitors_bp

    app.register_blueprint(users_bp)
    app.register_blueprint(monitors_bp)
    # Keep existing /api/v1/monitors for the personal workspace.
    # Team monitors live at /api/v1/workspaces/<workspace_id>/monitors.
    app.register_blueprint(monitors_bp, name="workspace_monitors",
                           url_prefix="/api/v1/workspaces/<workspace_id>/monitors")
    from sitesentinel.api.workspaces import bp as workspaces_bp
    app.register_blueprint(workspaces_bp)

    from sitesentinel.api.audits import bp as audits_bp
    app.register_blueprint(audits_bp)
    app.register_blueprint(
        audits_bp, name="workspace_audits",
        url_prefix="/api/v1/workspaces/<workspace_id>/monitors/<monitor_id>/audits",
    )

    from sitesentinel.monitoring.worker import worker_command
    app.cli.add_command(worker_command)

    @jwt.token_in_blocklist_loader
    def token_is_revoked(_header: dict, payload: dict) -> bool:
        # All tokens carry the user's current auth version.
        user = db.session.get(User, payload["sub"])
        if user is None or not user.is_active or payload.get("ver") != user.auth_version:
            return True
        if payload.get("type") == "refresh":
            return db.session.get(ConsumedRefreshToken, payload["jti"]) is not None
        return False

    @jwt.unauthorized_loader
    def missing_jwt(_reason: str):
        from sitesentinel.api.common import error_response
        return error_response("unauthorized", "Authentication is required.", 401)

    @jwt.invalid_token_loader
    def invalid_jwt(_reason: str):
        from sitesentinel.api.common import error_response
        return error_response("invalid_token", "Authentication token is invalid.", 422)

    @jwt.expired_token_loader
    def expired_jwt(_header: dict, _payload: dict):
        from sitesentinel.api.common import error_response
        return error_response("token_expired", "Authentication token has expired.", 401)

    @jwt.revoked_token_loader
    def revoked_jwt(_header: dict, _payload: dict):
        from sitesentinel.api.common import error_response
        return error_response("token_revoked", "Authentication token is no longer valid.", 401)

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        from sitesentinel.api.common import error_response
        return error_response("http_error", exc.description, exc.code or 500)

    @app.after_request
    def secure_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/v1/auth/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
