# SiteSentinel REST API — Flask foundation & monitoring engine

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

## Website monitoring API (Monitoring engine)

All monitoring endpoints require `Authorization: Bearer <access_token>`.
Monitor records belong to the authenticated user; another user's monitor ID always produces `404 not_found`.

### `POST /monitors` — 201

Request:

```json
{"name":"Example website","url":"https://example.com/","is_enabled":true}
```

Response:

```json
{"monitor":{"id":"<uuid>","name":"Example website","url":"https://example.com/","is_enabled":true,"last_status":null,"last_checked_at":null,"created_at":"<ISO-UTC>","updated_at":"<ISO-UTC>"}}
```

Only public HTTP/HTTPS URLs on ports 80/443 are permitted. Authentication, localhost, private addresses, and unsupported schemes are rejected. Duplicates within the same account return `409 duplicate_monitor`.

### Monitor endpoints

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/monitors?limit=50&offset=0` | List the current user's monitors, with `total` count; limit 1–200 |
| `GET` | `/monitors/{id}` | Monitor detail |
| `PATCH` | `/monitors/{id}` | Update any of `name`, `url`, `is_enabled`, `interval_seconds` |
| `DELETE` | `/monitors/{id}` | Delete monitor and its historical checks; 204 |
| `POST` | `/monitors/{id}/check` | Run a real, **synchronous** health check and save result; 201 |
| `GET` | `/monitors/{id}/checks?limit=25` | Most recent scan results; limit 1–100 |

### On-demand check response

```json
{
  "check": {
    "id": "<uuid>",
    "monitor_id": "<uuid>",
    "checked_url": "https://example.com/",
    "checked_at": "<ISO-UTC>",
    "status": "up",
    "status_code": 200,
    "response_time_ms": 141,
    "certificate_expires_at": "<ISO-UTC>",
    "final_url": "https://example.com/",
    "error": null
  }
}
```

Possible check statuses: `up` (HTTP 2xx/3xx), `down` (HTTP error, timeout, network/TLS failure), `blocked` (unsafe resolved address/redirect). For plain HTTP, certificate expiry is `null`. Certificate validation errors are reported as `down` rather than bypassing TLS verification. Disabled monitors return `409 monitor_disabled`.

**Execution model:** Manual API checks currently run inline (a bounded HTTP/HTTPS request with up to three validated redirects). Scheduled scans, basic retries, and incident alerts are handled by the separate worker; dashboards and distributed queues remain future features. Do not expose synchronous checks to untrusted traffic without rate limits and an operational review.

On URL changes, the latest status is reset. Historical checks retain their original `checked_url` for audit accuracy.

## Scheduled monitoring and incidents (Background jobs)

Scheduled checks run in a separate process, not on the Flask development server. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m flask --app sitesentinel monitor-worker --once
.\.venv\Scripts\python.exe -m flask --app sitesentinel monitor-worker --poll-seconds 10
```

The first command processes one batch and exits. The second keeps polling until `Ctrl+C`.
Run `flask --app sitesentinel db upgrade` before launching the worker. All workers and the API must use the **same** `DATABASE_URL` (on Windows, use your SQLite `.env`).

- `POST /monitors` also accepts `interval_seconds` (integer **60–86400**, default **300**) and `is_enabled`.
- `PATCH /monitors/{id}` accepts `interval_seconds` to change the schedule. Disabled monitors are not scanned.
- Monitor responses now include `interval_seconds`, `next_check_at`, and `failure_streak`.
- `GET /monitors/{id}/incidents?limit=25` returns outage intervals (`open` or `resolved`).
- `GET /monitors/{id}/alerts?limit=25` returns notification events (`down` or `recovered`) and delivery status.

**Incident policy:** Two consecutive results other than `up` open one incident and enqueue an alert. Further failures do not create duplicate incidents. The next successful check resolves the incident and queues a recovery alert. A URL change or monitor disable closes an outstanding incident without a false recovery alert. Existing historical checks retain their original URL.

### Optional email delivery

Alert events always persist in the database. To actually email the registered account address, configure SMTP in `.env`:

```dotenv
SMTP_HOST=smtp.example.net
SMTP_PORT=587
SMTP_FROM=notifications@example.net
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD=your-smtp-app-password
SMTP_STARTTLS=true
```

Without `SMTP_HOST`, alerts stay `pending` (visible via the API) and **no email is sent**. With SMTP enabled the worker attempts queued delivery and retries transient failures with backoff (up to 3 attempts). Delivery uses at-least-once semantics: duplicates are possible after a process crash during delivery.

**Operational limits:** This is a simple database-polling worker, not Celery/Redis. Run **one worker** when using SQLite; PostgreSQL is more appropriate for multi-process deployments. Keep worker and API on the same database, and add quotas, rate limits, and dedicated queue infrastructure before public deployment.
