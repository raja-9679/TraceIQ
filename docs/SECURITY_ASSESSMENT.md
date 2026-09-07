# Security assessment — internal pre-test baseline (2026-09-07)

Workstream I5 of `info/REGULATED_READINESS.md` is an **external** penetration
test followed by a SOC 2 Type II audit. Neither can be done from inside the
project. This page is what precedes them: the scope and rules of engagement a
tester needs, and an internal baseline (automated, against the running local
stack and the published images) so the external test starts from a clean
sheet rather than from findings a scanner would have produced in an hour.

## 1. Scope for the external test

**In scope**

| Surface | Where | Notes |
|---|---|---|
| Web application | frontend (nginx) + `/api/` proxy | React SPA, JWT in memory + refresh token; MFA; SSO callbacks (`/api/auth/sso/callback`, `/api/auth/saml/acs`) |
| REST API | `/api/*` (~230 routes, `backend/app/api/`) | JWT **and** API-key (`X-API-Key`) principals; RBAC at workspace/team/project; tenant isolation is application-layer only |
| SCIM 2.0 | `/scim/v2/*` and `/api/scim/v2/*` | bearer `SCIM_TOKEN`; 404 when unconfigured |
| Worker ↔ backend | `POST /api/runs/{id}/webhook`, `/finalize`, `/api/jobs/*`, `/api/internal/llm-usage` | `X-TraceIQ-Secret` / `X-Worker-Secret` shared secrets |
| Outbound | webhooks, OIDC/SAML metadata fetch, test targets | SSRF guard `app/core/net_guard.py`; `ALLOW_PRIVATE_NETWORK_TARGETS` |
| Artifact store | presigned S3 URLs from `MINIO_PUBLIC_URL` | object-key shape enforces run ownership (`GET /api/runs/{id}/artifact`) |
| MCP server | `integrations/mcp-server` (HTTP mode, `X-API-Key`) | 51 tools; write policy = proposal queue |
| Images | `traceiq-backend`, `traceiq-frontend`, `traceiq-execution-worker` | non-root (uid 10001), no default secrets |

**Out of scope / needs agreement:** the Playwright execution workers run
customer-authored steps against customer targets — a tester driving them is
testing the *customer's* app, not TraceIQ. `RAW_PLAYWRIGHT_ENABLED` is arbitrary
code execution by design and documented as such; test it only as a privilege
boundary (can a non-admin turn it on?), not as a vulnerability.

**Test accounts to provision:** one instance admin, one workspace admin, one
editor, one viewer in workspace A; one editor in workspace B (for isolation);
one API key scoped to a project; one SCIM token. Provide the tester the
`docs/ENTERPRISE_AUTH.md` and `docs/DATA_RESIDENCY.md` pages.

**Highest-value targets**, in the order I would point a tester at them:
tenant isolation across workspaces (every boundary is a Python `if`); the
proposal/auto-apply path (an agent-authored change reaching a test suite
without human review); SSO/SAML response handling; the artifact URL
authorization; refresh-token rotation and family revocation; the redaction
guarantees (does a captured header ever reach MinIO unredacted?).

## 2. Internal baseline — what was run

All against the `:dev` images built from this commit and the local community
stack (`http://localhost:8080`, nginx → backend), 2026-09-07.

| Tool | Target | Before | After the fixes below |
|---|---|---|---|
| OWASP ZAP baseline (passive, `-a`) | frontend + `/api/` | 0 High, **2 Medium** (no CSP; SRI on Google Fonts), 4 Low (COOP/COEP/CORP/Permissions-Policy missing) | 0 High, 3 Medium *accepted* (§4), 1 Low accepted |
| Trivy (HIGH/CRITICAL, OS + libs) | backend image | 92 High (13 fixable), 7 Critical (1 fixable: python-jose) | 92 High (13 fixable, all Python libs held by the FastAPI/OTel pins — §4), 6 Critical (0 fixable: Debian perl/sqlite/glib with no patched package) |
| Trivy | frontend image | 32 High (all fixable), 2 Critical (openssl, fixable) | **0 High, 0 Critical** |
| Trivy | execution-worker image | 81 High (77 fixable), 3 Critical (Go stdlib/grpc/x-crypto in the Playwright base image) | 65 High (61 fixable), 3 Critical — all in the Microsoft Playwright base image (Go binaries); moves with the next Playwright bump |
| pip-audit | backend deps | 38 advisories in 9 packages | 34 advisories in 9 packages — python-jose fixed; the rest are Starlette/FastAPI, protobuf (held by opentelemetry-proto), python-multipart 0.0.20 (newer advisories need 0.0.31, blocked by FastAPI 0.109), and build-time pip/setuptools/ecdsa/pyasn1 — see §4 |
| npm audit | frontend | 17 (12 high) | **0** |
| npm audit | execution-engine | 15 (9 high) | 8 (1 high: nodemailer; rest moderate) — remaining need major-version bumps; 86 engine tests still pass |
| gitleaks | full git history | 24 hits: 19 in `dump.sql`, 5 test-corpus | 5 test-corpus only, allowlisted; see `docs/CREDENTIAL_HYGIENE.md` |

## 3. Fixes made

- **Security headers** (`frontend/security-headers.conf`, included at every
  `add_header` site in `nginx.conf`): `Content-Security-Policy` with
  `script-src 'self'` and `object-src 'none'` (the SPA has no inline scripts,
  so this is a real XSS mitigation), `Permissions-Policy`,
  `Cross-Origin-Opener-Policy: same-origin`,
  `Cross-Origin-Resource-Policy: same-origin`. Existing: `X-Frame-Options DENY`,
  `nosniff`, `Referrer-Policy`, `server_tokens off`.
- **Python pins**: `python-jose` 3.3.0 → 3.4.0 (CVE-2024-33663, -33664),
  `python-multipart` 0.0.6 → 0.0.20 (DoS advisories), `requests` 2.31 → 2.32.4.
- **OS packages**: `apt-get upgrade` / `apk upgrade` in the backend and
  frontend Dockerfiles so a rebuild takes the distro's pending security fixes
  (the pinned base tags lag the archives).
- **npm**: `npm audit fix` (non-breaking) in `frontend/` and `execution-engine/`.
- **Secret scanning**: gitleaks CI job with `.gitleaks.toml` allowlisting the
  redaction test corpora by path.

## 4. Accepted / deferred, with reasons

- **CSP `style-src 'unsafe-inline'`** — Tailwind/Radix components set inline
  `style=` attributes; removing it needs a nonce pipeline through Vite. Medium
  value, non-trivial. Deferred.
- **CSP wildcard `img/media/connect-src https: http:`** — artifacts are served
  from the object store's public URL, which is deployment-specific. A
  deployment can tighten this in an nginx override. Documented in the snippet.
- **SRI on Google Fonts** — a dynamically generated stylesheet cannot carry an
  integrity hash. Fix is to self-host Inter; deferred.
- **COEP** — `require-corp` would block artifact images unless the object store
  also sends CORP headers. Deliberately not set.
- **Starlette / FastAPI advisories** — the remaining pip-audit entries
  (Starlette 0.35.1, fixed in 0.40–1.3; FastAPI 0.109.0) need a FastAPI major
  bump; the framework is used across ~230 routes and the change deserves its
  own tested branch, not a slot in a hygiene pass. `protobuf` 4.25 is held by
  `opentelemetry-proto` (<5). `pip`/`setuptools`/`ecdsa` in the image are
  build-time or transitive with no reachable path.
- **Base-image CVEs with no fix** (Debian `perl-base`, `libsqlite3`, `glib`;
  Playwright/Jammy Go binaries) — tracked by rebuilding; the worker image's Go
  findings live in the Microsoft Playwright base and go away with the next
  Playwright bump, which must move the npm package in lock-step.
- **`nodemailer`** high in the engine — used only for the legacy engine's
  email path; upgrade is a major version. Deferred to the next engine
  dependency pass.

## 5. What is *not* covered by this baseline

Authenticated dynamic scanning (ZAP active scan against the API with a session),
business-logic testing (isolation, proposal review bypass, SAML edge cases),
and anything requiring a human — which is exactly what the external test is
for. The unit and integration suites (tenant isolation, SAML mock IdP, audit
chain, redaction corpora) are the regression net under those areas.
