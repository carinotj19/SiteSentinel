"""Registration, authentication, single-use refresh, and global logout."""

from email_validator import EmailNotValidError, validate_email
from flask import Blueprint, jsonify
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    jwt_required,
)
from sqlalchemy.exc import IntegrityError

from sitesentinel.api.common import error_response, json_object
from sitesentinel.extensions import db
from sitesentinel.models import ConsumedRefreshToken, User

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def _normalize_email(value):
    if not isinstance(value, str):
        return None
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        return None


def _tokens(user: User) -> dict:
    claims = {"ver": user.auth_version}
    return {
        "access_token": create_access_token(identity=user.id, additional_claims=claims),
        "refresh_token": create_refresh_token(identity=user.id, additional_claims=claims),
        "token_type": "Bearer",
    }


@bp.post("/register")
def register():
    data = json_object()
    if data is None:
        return error_response("invalid_json", "A JSON object is required.", 400)

    email = _normalize_email(data.get("email"))
    name = data.get("display_name")
    password = data.get("password")
    if not email:
        return error_response("invalid_email", "Provide a valid email address.", 400)
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
        return error_response("invalid_name", "Display name must be 2–80 characters.", 400)
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        return error_response("invalid_password", "Password must be 12–128 characters.", 400)

    user = User(email=email, display_name=name.strip())
    user.set_password(password)
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error_response("email_taken", "An account with this email already exists.", 409)

    return jsonify({"user": user.public_dict()}), 201


@bp.post("/login")
def login():
    data = json_object()
    if data is None:
        return error_response("invalid_json", "A JSON object is required.", 400)
    email = _normalize_email(data.get("email"))
    password = data.get("password")
    if not email or not isinstance(password, str):
        return error_response("invalid_credentials", "Invalid email or password.", 401)
    user = db.session.scalar(db.select(User).filter_by(email=email))
    if not user or not user.is_active or not user.verify_password(password):
        return error_response("invalid_credentials", "Invalid email or password.", 401)
    return jsonify({"user": user.public_dict(), **_tokens(user)})


@bp.post("/refresh")
@jwt_required(refresh=True)
def refresh():
    user = db.session.get(User, get_jwt_identity())
    if user is None:
        return error_response("unauthorized", "Account no longer exists.", 401)
    # Unique jti + transaction stops two concurrent refreshes reusing one token.
    db.session.add(ConsumedRefreshToken(jti=get_jwt()["jti"]))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return error_response("token_revoked", "Refresh token has already been used.", 401)
    return jsonify(_tokens(user))


@bp.delete("/logout")
@jwt_required(verify_type=False)
def logout():
    # User-wide version change invalidates *both* access and refresh tokens.
    user = db.session.get(User, get_jwt_identity())
    if user is None:
        return error_response("unauthorized", "Account no longer exists.", 401)
    user.auth_version += 1
    db.session.commit()
    return "", 204
