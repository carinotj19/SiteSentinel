"""Authenticated account management."""

from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from sitesentinel.api.common import error_response, json_object
from sitesentinel.extensions import db
from sitesentinel.models import User

bp = Blueprint("users", __name__, url_prefix="/api/v1/users")


@bp.get("/me")
@jwt_required()
def get_profile():
    user = db.session.get(User, get_jwt_identity())
    return jsonify({"user": user.public_dict()})


@bp.patch("/me")
@jwt_required()
def update_profile():
    data = json_object()
    if data is None:
        return error_response("invalid_json", "A JSON object is required.", 400)
    # Explicit allowlist: clients may not edit email, role, auth_version or is_active.
    if set(data) != {"display_name"}:
        return error_response("invalid_fields", "Only display_name can be updated.", 400)
    name = data["display_name"]
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
        return error_response("invalid_name", "Display name must be 2–80 characters.", 400)
    user = db.session.get(User, get_jwt_identity())
    user.display_name = name.strip()
    db.session.commit()
    return jsonify({"user": user.public_dict()})
