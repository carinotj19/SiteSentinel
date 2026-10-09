"""Consistent JSON errors and request validation."""

from flask import jsonify, request


def error_response(code: str, message: str, status: int):
    return jsonify({"error": {"code": code, "message": message}}), status


def json_object():
    """Return a dict or None if request data is not a JSON object."""
    if not request.is_json:
        return None
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None
