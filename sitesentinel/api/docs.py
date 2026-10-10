"""Serve the repository's OpenAPI contract and a small Swagger UI shell.

The API specification is local. Swagger UI's JS/CSS are fetched from a pinned
external CDN; the plain JSON spec is available without JavaScript or a CDN.
"""

from pathlib import Path

from flask import Blueprint, Response, send_file

bp = Blueprint("api_docs", __name__, url_prefix="/api")
_SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
_SWAGGER_VERSION = "5.33.1"


@bp.get("/openapi.json")
def openapi_spec():
    response = send_file(_SPEC_PATH, mimetype="application/json")
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/docs")
def swagger_ui():
    asset_base = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{_SWAGGER_VERSION}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SiteSentinel API Documentation</title>
  <link rel="stylesheet" href="{asset_base}/swagger-ui.css">
  <style>body {{ margin: 0; background: #fafafa; }}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{asset_base}/swagger-ui-bundle.js"></script>
  <script>
    window.onload = () => {{
      SwaggerUIBundle({{
        url: "/api/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayRequestDuration: true,
        persistAuthorization: false
      }});
    }};
  </script>
</body>
</html>
"""
    response = Response(html, mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        f"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        f"script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
        "connect-src 'self'; img-src 'self' data:; font-src 'self' data: https://cdn.jsdelivr.net; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    return response
