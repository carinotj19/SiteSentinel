# Local read-only portfolio demo

This is the recommended **controlled** demonstration for a recruiter or technical
interviewer. The demo uses the real Flask API, JWT login, database models,
incident-transition logic and reporting endpoints, but starts with **synthetic
sample history** and does not access any external websites.

It does **not** modify your existing SQLite database, .env, or
PostgreSQL database. No migrations, Docker, SMTP credentials, or worker are needed.

## Windows walkthrough

1. If you have Flask running already on port 8000, press **Ctrl+C** in its
   PowerShell terminal. Start a new PowerShell window.
2. From the repository root:

```powershell
cd E:\Repo\SiteSentinel
.\.venv\Scripts\python.exe -m sitesentinel.portfolio_demo
```

3. Open **http://127.0.0.1:8000/api/docs** in your browser.
4. In Swagger UI expand **Authentication → POST /api/v1/auth/login**, choose
   **Try it out** and enter:

```json
{
  "email": "demo@example.com",
  "password": "portfolio-demo-only-2026"
}
```

5. Click **Execute**. Copy the returned **access_token** (not refresh_token).
   Click Swagger's **Authorize** control and paste the access token. Depending
   on the Swagger UI version, you may need to enter the raw token without the
   Bearer prefix.
6. Try these authenticated GET requests:
   - /api/v1/reports/overview — two personal demo monitors, including an outage
   - /api/v1/reports/response-times — daily response-time summaries
   - /api/v1/reports/incidents — active checkout outage
   - /api/v1/reports/audit-highlights — simulated SEO and broken links
   - /api/v1/monitors — personal monitor IDs and status
   - /api/v1/workspaces — personal and team workspace IDs
   - /api/v1/workspaces/{workspace_id}/reports/overview — substitute the
     **Demo Agency Workspace** ID to view separate team data
   - /api/v1/workspaces/{workspace_id}/reports/incidents — recently
     resolved blog incident for the team

The sample data includes healthy checks, consecutive failures, a still-open
incident, a recovered incident, queued alerts, and completed/failed audit
snapshots. Values change only because each run generates timestamps relative
to startup, not because the demo fetches websites.

To restart from scratch, press **Ctrl+C**, then rerun the module. Everything
is erased when the process exits.

If port 8000 is in use:

```powershell
.\.venv\Scripts\python.exe -m sitesentinel.portfolio_demo --port 8010
```

Then browse to **http://127.0.0.1:8010/api/docs**.

## Security boundaries

- The showcase binds to **127.0.0.1 only**, never 0.0.0.0.
- Flask debug mode and auto-reload are disabled.
- Data lives in **SQLite in memory**, not your actual application database.
- The JWT signing key is random for every run; environment secrets are ignored.
- SMTP is disabled; **no monitoring worker** is started.
- All non-read methods return **403 demo_read_only**, except token login and
  refresh. This means creating monitors, triggering real checks/audits,
  registration, editing, deletion and sending invitations cannot run.
- Swagger UI loads its display assets from a pinned third-party CDN. The API
  JSON is served locally even if that CDN is inaccessible.
- The published demo credentials are intentionally **local-only**. Do not
  expose this demo to the public internet, port-forward it, or tunnel it
  through a public reverse proxy. Do not host the demo as an unrestricted
  cloud service.

The regular application remains unchanged. To try real network monitoring,
use the normal app with your own configuration, after reviewing the
[security review](SECURITY_REVIEW.md).

## What this demo proves

The seeded records flow through the same database tables, JWT authorization,
workspace isolation, status/incident transition logic, and reporting queries
used by the regular application. The demo provides a repeatable, private
portfolio walkthrough without misleading viewers into thinking the sample
measurements were captured from real websites.
