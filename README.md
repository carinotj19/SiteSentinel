# SiteSentinel

A Flask-first backend for a website-monitoring SaaS. **Flask foundation** is a testable REST API foundation: PostgreSQL models/migrations, user authentication, account management, Docker-based local development, and CI. Monitoring workers and scanning are intentionally **not implemented yet**; those belong to the upcoming monitoring-engine and background-jobs features.

## Stack

- Python 3.12, Flask 3.1, Flask blueprints + application factory
- Flask-SQLAlchemy, PostgreSQL, Alembic / Flask-Migrate
- JWT access tokens (15 min) and refresh tokens (7 days)
- scrypt password hashing via Werkzeug
- Pytest, Ruff, GitHub Actions
- Docker Compose + Gunicorn

## Quick start (Windows PowerShell or Unix shell)

1. Clone the repository, enter its directory, and make an environment file:

   ```powershell
   git clone https://github.com/carinotj19/SiteSentinel.git
   cd SiteSentinel
   Copy-Item .env.example .env
   ```

   On macOS/Linux, use `cp .env.example .env`. Replace `JWT_SECRET_KEY` in `.env` with a randomly generated value:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Start PostgreSQL and the API:

   ```powershell
   docker compose up --build
   ```

   The API is at **http://127.0.0.1:8000**. Compose waits for PostgreSQL readiness and runs committed database migrations before starting Gunicorn.

3. Verify it is alive and the database is ready:

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/live
   Invoke-RestMethod http://127.0.0.1:8000/api/v1/health/ready
   ```

4. Register and sign in:

   ```powershell
   $body = @{ email = "you@example.com"; display_name = "Site Owner"; password = "choose-a-long-password-123" } | ConvertTo-Json
   Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/register -Method Post -ContentType application/json -Body $body
   $login = Invoke-RestMethod http://127.0.0.1:8000/api/v1/auth/login -Method Post -ContentType application/json -Body (@{email="you@example.com";password="choose-a-long-password-123"}|ConvertTo-Json)
   Invoke-RestMethod http://127.0.0.1:8000/api/v1/users/me -Headers @{ Authorization = "Bearer $($login.access_token)" }
   ```

## Local tests (no PostgreSQL required)

Tests use an isolated in-memory SQLite database. For Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
ruff check .
pytest -q --cov=sitesentinel
```

For macOS/Linux use `python3.12 -m venv .venv` and `source .venv/bin/activate`.

## REST endpoints

| Verb | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/health/live` | None | API liveness |
| GET | `/api/v1/health/ready` | None | Database readiness |
| POST | `/api/v1/auth/register` | None | Create user (email, display_name, password) |
| POST | `/api/v1/auth/login` | None | Obtain access and refresh JWTs |
| POST | `/api/v1/auth/refresh` | Refresh JWT | Rotate refresh JWT; old refresh becomes unusable |
| DELETE | `/api/v1/auth/logout` | Either JWT | Log out all sessions by invalidating both token types |
| GET | `/api/v1/users/me` | Access JWT | Current account |
| PATCH | `/api/v1/users/me` | Access JWT | Change display_name only |

Errors have a stable `{ "error": { "code": "...", "message": "..." } }` shape. Full examples are in [docs/API.md](docs/API.md).

## Structure

```text
sitesentinel/
  __init__.py       # Flask application factory, JWT callbacks, JSON errors
  config.py         # Environment configuration
  extensions.py     # Unbound Flask extensions
  models.py         # User + refresh-token rotation entities
  api/              # Auth, users, health blueprints
migrations/         # Committed Alembic migration history
tests/              # Isolated API tests
.github/workflows/  # Automated lint/test/compile CI
compose.yaml        # Development API + PostgreSQL
Dockerfile          # Gunicorn container
```

## Engineering notes

- **Token revocation:** logout increments the account's `auth_version`; the JWT callback checks it on every authenticated request. Refresh JWTs are single-use and recorded in `consumed_refresh_tokens` so they cannot be replayed. A future scheduled cleanup will delete expired JTIs; Flask foundation does not yet perform that maintenance.
- **Secrets:** never commit `.env`, JWT keys, or production credentials. Docker Compose's password is development-only. Configure different credentials and TLS before external deployment.
- **Authentication hardening before public deployment:** add rate limiting, password reset/verification, abuse monitoring, account recovery, and a secure deployment configuration. Browser-based token storage requires separate XSS/CSRF review; Flask foundation only accepts `Authorization: Bearer` headers.
- **Schema:** use `flask --app sitesentinel db upgrade` for migrations; do not run `db.create_all()` in real environments. SQLite tests use `create_all()` solely as a fixture.
- **No fake scan data:** the monitoring engine, user-defined monitors, team permissions, and dashboards are later milestones.

## Feature roadmap

- [x] **Flask foundation** — Flask API, authentication, SQLAlchemy models, migrations, Docker, tests, CI (`feat/foundation`)
- [ ] **Monitoring engine** — Website checks, SSL certificate inspection, result history, and SSRF protections (`feat/monitoring-engine`)
- [ ] **Background jobs** — Celery workers, scheduling, alerts, and retries (`feat/background-jobs`)
- [ ] **Workspaces and access control** — Organizations, memberships, RBAC, and API keys (`feat/workspaces-rbac`)
- [ ] **Website audits and dashboard** — Broken links, SEO, tracking audits, dashboards, reports, and deployment hardening (`feat/site-audits`)

Feature branches are named by what they deliver, not by a numbered phase. Prefer clear PR titles such as **Flask foundation** or **Monitoring engine**.

> Flask foundation is a development foundation, not an audited production authentication system.
