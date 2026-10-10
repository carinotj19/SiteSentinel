"""OpenAPI contract and Swagger UI are part of the tested API surface."""

import re

from openapi_spec_validator import validate_spec


def _operations(document):
    methods = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
    return {
        (path, method)
        for path, operations in document["paths"].items()
        for method in operations
        if method in methods
    }


def _route_operations(app):
    """Convert Flask URL converters to OpenAPI named path placeholders."""
    operations = set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/"):
            continue
        if rule.rule in {"/api/openapi.json", "/api/docs", "/api/swagger-init.js"}:
            continue
        path = re.sub(r"<(?:[a-z]+:)?([a-z_]+)>", r"{\1}", rule.rule)
        for method in (rule.methods or set()) - {"HEAD", "OPTIONS"}:
            operations.add((path, method.lower()))
    return operations


def test_openapi_spec_is_valid_and_all_registered_api_methods_are_documented(app, client):
    response = client.get("/api/openapi.json")
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    contract = response.json

    validate_spec(contract)
    assert contract["openapi"].startswith("3.1.")
    assert _operations(contract) == _route_operations(app)
    # Public routes must not accidentally inherit JWT requirements.
    for path, method in (
        ("/api/v1/health/live", "get"),
        ("/api/v1/health/ready", "get"),
        ("/api/v1/auth/register", "post"),
        ("/api/v1/auth/login", "post"),
    ):
        assert contract["paths"][path][method]["security"] == []
    assert contract["security"] == [{"bearerAuth": []}]
    assert len(_operations(contract)) == 51


def test_documentation_ui_is_public_but_does_not_bypass_auth(client):
    page = client.get("/api/docs")
    assert page.status_code == 200
    assert "swagger-ui" in page.get_data(as_text=True)
    assert "/api/openapi.json" in client.get("/api/swagger-init.js").get_data(as_text=True)
    assert "persistAuthorization: false" in client.get("/api/swagger-init.js").get_data(as_text=True)
    assert "unsafe-inline" not in page.headers["Content-Security-Policy"].split("script-src")[1].split(";")[0]
    assert client.get("/api/v1/monitors").status_code == 401
    assert client.get("/api/v1/workspaces").status_code == 401
    assert client.get("/api/v1/reports/overview").status_code == 401


def test_api_spec_models_token_rotation_roles_scans_and_static_audit(client):
    spec = client.get("/api/openapi.json").json
    schemas = spec["components"]["schemas"]
    assert schemas["Tokens"]["properties"]["refresh_token"]["type"] == "string"
    assert schemas["Workspace"]["properties"]["role"]["enum"] == [
        "owner", "admin", "member", "viewer"
    ]
    create = spec["paths"]["/api/v1/workspaces/{workspace_id}/monitors"]["post"]
    assert {"workspace_id"} == {
        param["name"] for param in create["parameters"] if param["in"] == "path"
    }
    assert create["requestBody"]["content"]["application/json"]["schema"]["required"] == [
        "name", "url"
    ]
    check = spec["paths"]["/api/v1/monitors/{monitor_id}/check"]["post"]
    assert "409" in check["responses"]
    assert "503" in check["responses"]

    scan = spec["paths"]["/api/v1/monitors/{monitor_id}/audits"]["post"]
    link_limit = scan["requestBody"]["content"]["application/json"]["schema"]["properties"]["link_limit"]
    assert (link_limit["minimum"], link_limit["maximum"]) == (0, 10)
    assert "refresh" in spec["paths"]["/api/v1/auth/refresh"]["post"]["description"].lower()
    assert "duration-weighted" in spec["info"]["description"]
