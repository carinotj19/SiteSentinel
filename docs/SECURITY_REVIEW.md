# SiteSentinel — security and portfolio-readiness review

Reviewed: 2026-10-10. Scope: authenticated Flask APIs, workspace access control,
HTTP(S) monitoring/audits, background worker/email, deployment configuration.
This is a targeted **source review and automated regression test pass**, not a
third-party penetration test or a promise of production security.

## Findings addressed in this review

| Finding | Severity | Resolution |
| --- | --- | --- |
| Pending alert emails used the original monitor creator even after they lost access to the team workspace | High (confidentiality) | Before constructing/sending, check the account is active and still a member. Unauthorized queued alerts are marked failed, not sent. |
| Manual checks could overlap worker scans or save results after URL edits, disables, or permission removal | Medium (data integrity / authorization) | Manual scans claim the existing monitor lease, reload and reauthorize after network I/O, and discard stale outcomes. |
| Site audits could persist results obtained from a previous URL or after the initiator's permissions changed | Medium (data integrity / authorization) | Capture requested URL and reauthorize/revalidate monitor state after audit I/O, before persisting. |

The fixes use existing SQLAlchemy columns/tables; no new schema migration is required.
Tests use mocked network and SMTP calls. They are safe for local SQLite and CI.

## Existing defenses verified by source inspection

- HTTP(S) URLs are normalized before scan, with credentials and nonstandard ports prohibited.
- DNS answers must be globally routable; mixed private/public answers fail.
- Requests use IP-pinned connections while preserving TLS hostname verification.
- Each redirect receives fresh URL and DNS validation; redirect count is bounded.
- HTML audits limit response bytes and same-origin checked links and do not execute JavaScript.
- JWT access and refresh lifetimes are bounded; refresh JWTs are single-use.
- API resource lookup scopes monitors, audit results and reports to workspace membership.
- Admin/owner roles and owner protections are tested; raw invitation tokens are not stored.

These are review observations, not a full validation against a hostile deployment.

## Remaining blockers before a public, internet-facing deployment

**High priority**
1. Email verification / invitation identity proof: accounts can currently register
   with any syntactically valid email address. Do not rely on email string equality
   as proof of inbox ownership; enable verified-email onboarding before public
   invitations or sensitive notification access.
2. Abuse prevention: registration, login, manual scans, and audits need rate limiting
   and per-user/workspace quotas enforced in a shared backend (e.g., Redis or
   persistent database); do not assume an in-process rate limiter is safe with
   multiple workers.
3. Job isolation and total time budgets: synchronous manual checks and audits can
   tie up web workers. Socket timeouts alone are not a full request wall-clock
   deadline, especially across DNS, redirects, and multiple links. Move untrusted
   scans to a restricted background queue with strict time/CPU/network budgets.
4. Public HTTP scanner network containment: application-level DNS/IP checks are
   defense-in-depth, not a sandbox. Restrict egress with deployment firewall/network
   controls and consider DNS rebinding, proxy and infrastructure differences.

**Operational priorities**
5. Deploy the scan worker as its own managed service; the included Compose
   configuration only runs the web API and database.
6. Redis/queue or PostgreSQL-backed distributed coordination is recommended for
   production. SQLite worker instructions are intentionally single-process.
7. Enforce HTTPS at the edge, rotate and manage JWT/SMTP secrets, configure trusted
   proxy headers carefully, and add security/operational logging.
8. Review dependency advisories, enable automated dependency updates, and scan
   container images in CI.
9. Long-term data retention, endpoint-level request accounting, email delivery
   ownership policy, workspace offboarding, and account recovery need product decisions.

## Portfolio demo deployment policy

Until the above blockers are addressed, use **local development** or a
**private/authenticated demonstration environment**. Do not advertise the API
as a secure public SaaS, even if the GitHub Actions tests pass.

A strong technical portfolio presentation can explain this architecture,
demonstrate safe development workflows and tests, and transparently distinguish
implemented defenses from the hardening needed for production deployment.
