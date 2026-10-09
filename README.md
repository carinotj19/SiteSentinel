# SiteSentinel

**A Flask-powered website monitoring platform** for keeping an eye on site availability, HTTPS certificates, and operational health.

[![Backend CI](https://github.com/carinotj19/SiteSentinel/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/carinotj19/SiteSentinel/actions/workflows/ci.yml)

> **Project status:** Active development. The Flask foundation (accounts, authentication, database migrations, automated tests) is implemented. Website monitoring is the next feature. This is a learning/portfolio project, **not** a production monitoring service yet.

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
| Uptime, response-time, and SSL checks | Planned: monitoring engine |
| Persistent scan history and monitor CRUD | Planned: monitoring engine |
| Scheduled checks, background workers, and alerts | Planned: background jobs |
| Team workspaces, RBAC, and API keys | Planned: workspaces |
| Broken links, SEO, tracking audits, and dashboards | Planned: site audits |

No monitoring results or dashboards are simulated or claimed as working before their implementations land.

## Tech stack

| Area | Tools |
| --- | --- |
| API | Python 3.11+, Flask 3.1, Flask Blueprints |
| Database | PostgreSQL (deployment), SQLite (local development/tests) |
| ORM and migrations | Flask-SQLAlchemy, Flask-Migrate / Alembic |
| Authentication | Flask-JWT-Extended, Werkzeug password hashing |
| Testing and linting | Pytest, pytest-cov, Ruff |
| Tooling | GitHub Actions, Docker Compose (optional) |

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
- [ ] **Monitoring engine** (`feat/monitoring-engine`) — Secure website management, uptime/SSL checks, historical results
- [ ] **Background jobs** (`feat/background-jobs`) — Celery/Redis, periodic monitoring, incident alerting
- [ ] **Workspaces & permissions** (`feat/workspaces-rbac`) — Organizations, memberships, roles, API keys
- [ ] **Site audits & dashboard** (`feat/site-audits`) — Broken links, SEO, tracking scripts, reports

## Security and operational notes

- SiteSentinel hashes passwords, restricts profile updates, and uses short-lived JWT access tokens with single-use refresh-token rotation.
- This foundation is **not production-hardened**: rate limiting, account recovery, email verification, abuse detection, TLS deployment, and operational monitoring are future work.
- Never expose Flask's debug server publicly. Keep secrets outside source control.
- User-supplied URL scanning will require SSRF defenses before exposing any monitoring endpoint.

## Contributing

Development is currently focused on building the core features. Issues and focused pull requests are welcome when the repository is open to collaborators. Use feature-oriented branch names such as `feat/monitoring-engine` and include tests for behavior changes.

## License

No license has been selected yet. Please do not assume the source code is available for reuse under an open-source license.
