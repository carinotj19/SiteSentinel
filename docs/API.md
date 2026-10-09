# SiteSentinel REST API — Flask foundation

Base URL: `http://127.0.0.1:8000/api/v1`

JSON requests require `Content-Type: application/json`. All protected endpoints accept JWTs via the `Authorization: Bearer <token>` header. Passwords must be 12–128 characters. Account email uniqueness is case-insensitive under SiteSentinel's normalization policy.

## POST /auth/register — 201

Request:

```json
{"email":"alice@example.com","display_name":"Alice","password":"example-long-password-123"}
```

Response (no password or tokens):

```json
{"user":{"id":"<uuid>","email":"alice@example.com","display_name":"Alice","created_at":"<ISO-UTC>","updated_at":"<ISO-UTC>"}}
```

Validation errors: `400 invalid_json`, `400 invalid_email`, `400 invalid_name`, `400 invalid_password`, `409 email_taken`.

## POST /auth/login — 200

Request: `{"email":"alice@example.com","password":"example-long-password-123"}`.

Response: `{ "user": { ... }, "access_token": "<jwt>", "refresh_token": "<jwt>", "token_type": "Bearer" }`.

Invalid login attempts return `401 invalid_credentials` without disclosing whether the address exists.

## POST /auth/refresh — 200

Authorization: **refresh** JWT. Empty JSON body is not needed. Returns `access_token`, `refresh_token`, `token_type`. The submitted refresh token is consumed; using it again returns `401 token_revoked`.

## DELETE /auth/logout — 204

Authorization: **access or refresh** JWT. Invalidates all tokens belonging to the account (including those issued in other sessions). Log in again to continue.

## GET /users/me — 200

Authorization: **access** JWT. Returns `{ "user": { ... } }`.

## PATCH /users/me — 200

Authorization: **access** JWT. Request: `{"display_name":"New Name"}`. Only this field is writable. Unknown or mixed fields return `400 invalid_fields`.

## GET /health/live and /health/ready

Liveness: `{"status":"ok","service":"sitesentinel"}`. Readiness: `{"status":"ready","database":"ok"}` or HTTP 503 `{"status":"unavailable","database":"unavailable"}` when the database cannot be queried.

## JSON errors

```json
{"error":{"code":"token_revoked","message":"Authentication token is no longer valid."}}
```

Unauthenticated requests return `401 unauthorized`; invalid token syntax returns `422 invalid_token`; expired/revoked tokens return `401` with their specific codes. Unknown routes return `404 http_error`.
