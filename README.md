# SiteSentinel

**A Flask-powered website monitoring platform** for keeping an eye on site availability, HTTPS certificates, and operational health.

[![Backend CI](https://github.com/carinotj19/SiteSentinel/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/carinotj19/SiteSentinel/actions/workflows/ci.yml)

> **Project status:** Active development. The Flask foundation, on-demand monitoring, and a standalone scheduled-check worker with incident and alert history are implemented. Optional SMTP delivery is available; distributed queuing and the UI remain future work. This is a learning/portfolio project, **not** a production monitoring service yet.

## Overview

SiteSentinel aims to give developers and small teams a single place to register websites, monitor their health, inspect failures, and eventually automate alerts and audits. It's being built as a backend-focused project demonstrating practical API design, data modeling, authentication, background jobs, testing, and deployment.

### Features

| Capability | Status |
| --- | --- |
| User registration and authentication (JWT) | Implemented |
| Refresh-token rotation and logout across sessions | Implemented |
| Account profile API | Implemented |
| SQLAlchemy models and Alembic migrations | Implemented |
| API health and database-readiness endpoints | Implemented |
| GitHub Actions CI and automated tests | Implemented |
| On-demand uptime, response-time, and SSL checks | Implemented |
| User-owned monitors and persisted check history | Implemented |
| Database-backed scheduled checks, incidents, and alert events | Implemented |
| Optional SMTP email delivery and retry | Implemented (configure SMTP) |
| Team workspaces, RBAC, and API keys | Planned: workspaces |
| Broken links, SEO, tracking audits, and dashboards | Planned: site audits |

Scans make real requests to validated public HTTP/HTTPS targets. No monitoring results or dashboards are simulated.

## Tech stack

| Area | Tools |
| --- | --- |
| API | Python 3.11+, Flask 3.1, Flask Blueprints |
| Database | PostgreSQL (deployment), SQLite (local development/tests) |
| ORM and migrations | Flask-SQLAlchemy, Flask-Migrate / Alembic |
| Authentication | Flask-JWT-Extended, Werkzeug password hashing |
| Testing and linting | Pytest, pytest-cov, Ruff |
| Tooling | GitHub Actions, separate Python worker, Docker Compose (optional) |

## Getting started

### Local development (Windows, **no Docker required**)

**Prerequisites:** Python 3.11+ and Git. Clone the repository, or use your existing checkout.

```powershell
git clone https://github.com/carinotj19/SiteSentinel.git
cd SiteSentinel
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Edit `.env` and set:

```dotenv
DATABASE_URL=sqlite:///sitesentinel.db
JWT_SECRET_KEY=replace-this-with-your-own-random-secret
```

Generate a secret with:

```powershell
py -3.11 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the generated value into `.env`. **Never commit `.env` or share tokens.** If you previously set `DATABASE_URL` in PowerShell, change that variable too: environment variables override the values loaded from `.env`.

Initialize the database and start the development server:

```powershell
.\.venv\Scripts\python.exe -m flask --app sitesentinel db upgrade
.\.venv\Scripts\python.exe -m flask --app sitesentinel run --debug --port 8000
```

The API listens at `http://127.0.0.1:8000`. Debug mode is **only for local development**.

### Linux / macOS (SQLite)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Update DATABASE_URL and JWT_SECRET_KEY in .env as shown above
flask --app sitesentinel db upgrade
flask --app sitesentinel run --debug --port 8000
```

### Docker (optional, PostgreSQL)

The included `compose.yaml` runs the API with a PostgreSQL service. After configuring `.env` with the example's Docker PostgreSQL URL and a real `JWT_SECRET_KEY`:

```bash
docker compose up --build
```

This path is optional; you can develop and run tests without Docker.

## API quick start

Check readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
```

Register and sign in (replace the example credentials):

```powershell
$body = @{email="you@example.com";display_name="Site Owner";password="change-me-to-a-long-password"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method POST -ContentType "application/json" -Body $body

$login = Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/login -Method POST -ContentType "application/json" -Body (@{email="you@example.com";password="change-me-to-a-long-password"} | ConvertTo-Json)
Invoke-RestMethod http://127.0.0.1:8000/api/v1/users/me -Headers @{Authorization="Bearer $($login.access_token)"}
```

### Available endpoints

| Method | Route | Access |
| --- | --- | --- |
| `GET` | `/api/v1/health/live` | Public |
| `GET` | `/api/v1/health/ready` | Public |
| `POST` | `/api/v1/auth/register` | Public |
| `POST` | `/api/v1/auth/login` | Public |
| `POST` | `/api/v1/auth/refresh` | Refresh token |
| `DELETE` | `/api/v1/auth/logout` | Access or refresh token |
| `GET` | `/api/v1/users/me` | Access token |
| `PATCH` | `/api/v1/users/me` | Access token |
| `GET`, `POST` | `/api/v1/monitors` | Access token |
| `GET`, `PATCH`, `DELETE` | `/api/v1/monitors/{id}` | Access token |
| `POST` | `/api/v1/monitors/{id}/check` | Access token |
| `GET` | `/api/v1/monitors/{id}/checks` | Access token |
| `GET` | `/api/v1/monitors/{id}/incidents` | Access token |
| `GET` | `/api/v1/monitors/{id}/alerts` | Access token |

### Run a website check

Using your `$login` token from the login example above:

```powershell
$headers = @{Authorization = "Bearer $($login.access_token)"}
$body = @{name = "Example website"; url = "https://example.com"} | ConvertTo-Json
$monitor = Invoke-RestMethod http://127.0.0.1:8000/api/v1/monitors -Method POST -Headers $headers -ContentType "application/json" -Body $body
$id = $monitor.monitor.id
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/monitors/$id/check" -Method POST -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/monitors/$id/checks" -Headers $headers
```

Manual checks run on demand; **scheduled checks** run in the separate `monitor-worker` process. Results include HTTP status, response time, and HTTPS certificate expiration when available. Internal/private hosts and unsafe redirects are blocked. Only ports 80 and 443 are supported.

### Automatic monitoring (no Docker required)

After running `flask --app sitesentinel db upgrade`, start a **second PowerShell window** in the repository root:

```powershell
.\\.venv\\Scripts\\python.exe -m flask --app sitesentinel monitor-worker --once
# Or keep monitoring in the background until Ctrl+C:
.\\.venv\\Scripts\\python.exe -m flask --app sitesentinel monitor-worker --poll-seconds 10
```

The worker uses your same `.env` SQLite database and defaults to checking enabled websites every **300 seconds**. Use `interval_seconds` (60–86400) when creating or editing a monitor. An incident opens after **two consecutive failures**; one recovery closes it. Events remain available from `GET /api/v1/monitors/{id}/incidents` and `GET /api/v1/monitors/{id}/alerts`.

Email is **optional**. Without SMTP configuration, notifications are saved as pending events and no email is sent. To enable email, configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_STARTTLS` in `.env` (see [API documentation](docs/API.md)). Never commit credentials. SQLite users should run only **one** worker process.

Detailed request/response examples: [API documentation](docs/API.md).

## Testing and quality

Run locally on Windows from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

The API test suite uses an isolated SQLite database, so neither Docker nor PostgreSQL is required. GitHub Actions also runs migration checks against PostgreSQL.

## Project structure

```text
SiteSentinel/
├── .github/workflows/   # CI
├── docs/                # API reference
├── migrations/          # Versioned database migrations
├── sitesentinel/
│   ├── api/             # Versioned Flask blueprints
│   ├── monitoring/      # SSRF-conscious HTTP/TLS scanner
│   ├── __init__.py      # Application factory
│   ├── config.py        # Environment-based settings
│   ├── extensions.py    # Flask extension instances
│   └── models.py        # Database models
├── tests/               # Pytest suite
├── .env.example         # Safe configuration template
├── compose.yaml         # Optional PostgreSQL + API setup
├── Dockerfile
└── requirements-dev.txt
```

## Roadmap

- [x] **Flask foundation** — API architecture, database, authentication, migration, tests
- [x] **Monitoring engine** (`feat/monitoring-engine`) — Secure website management, uptime/SSL checks, historical results
- [x] **Background jobs** (`feat/background-jobs`) — Database-polling worker, periodic monitoring, incident tracking, optional SMTP alerts
- [ ] **Distributed queue** — Celery/Redis and production queue orchestration for higher-scale deployments
- [ ] **Workspaces & permissions** (`feat/workspaces-rbac`) — Organizations, memberships, roles, API keys
- [ ] **Site audits & dashboard** (`feat/site-audits`) — Broken links, SEO, tracking scripts, reports

## Security and operational notes

- SiteSentinel hashes passwords, restricts profile updates, and uses short-lived JWT access tokens with single-use refresh-token rotation.
- This foundation is **not production-hardened**: rate limiting, account recovery, email verification, abuse detection, TLS deployment, and operational monitoring are future work.
- Never expose Flask's debug server publicly. Keep secrets outside source control.
- URL scans reject private/non-global DNS answers, restrict schemes and ports, pin public IPs, validate TLS, and re-check redirects. This is an initial SSRF defense, **not** a guarantee of production safety.
- Manual checks run synchronously. The separate worker polls the database and uses expiring monitor leases; for local SQLite development, run a single worker. Add distributed queues, rate limits, quotas, request budgets, and operational security review before public exposure.

## Contributing

Development is currently focused on building the core features. Issues and focused pull requests are welcome when the repository is open to collaborators. Use feature-oriented branch names such as `feat/monitoring-engine` and include tests for behavior changes.

## License

No license has been selected yet. Please do not assume the source code is available for reuse under an open-source license.
