"""Separate liveness and database readiness probes."""

from flask import Blueprint, jsonify
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sitesentinel.extensions import db

bp = Blueprint("health", __name__, url_prefix="/api/v1/health")


@bp.get("/live")
def live():
    return jsonify({"status": "ok", "service": "sitesentinel"})


@bp.get("/ready")
def ready():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ready", "database": "ok"})
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"status": "unavailable", "database": "unavailable"}), 503
