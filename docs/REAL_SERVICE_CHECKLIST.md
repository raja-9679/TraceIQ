# Real-service verification checklist (WP15c, decision H5)

Several integrations are built against the vendors' documented APIs but have
only been tested with mocks and fakes, because testing them for real needs an
account the project does not have. The E2E suite (`e2e/`) cannot cover them
either: it runs against a throwaway stack with no internet dependencies. This
checklist is the manual step that closes that gap. Run it before a release that
claims one of these integrations works, and again after a change to that
integration.

| # | Integration | Mock-verified in | Owner provides |
|---|---|---|---|
| 1 | SAML 2.0 SSO | `tests/test_saml_auth.py` (self-signed mock IdP) | IdP tenant (Entra ID, Okta or Keycloak) with admin rights |
| 2 | PagerDuty paging | `paging._post` mocks | PagerDuty account + a service with an Events API v2 integration |
| 3 | Opsgenie paging | `paging._post` mocks | Opsgenie account (US or EU) + an API integration key |
| 4 | Jira requirements import + tickets | `tracker_import._TRANSPORT` (`httpx.MockTransport`) | Jira Cloud site, a project, an API token |
| 5 | Linear requirements import | same | Linear workspace + a personal API key |
| 6 | Splunk HEC (SIEM) | outbox/delivery unit tests | Splunk instance (Cloud trial or Enterprise) with a HEC token |
| 7 | Elastic (SIEM) | same | Elasticsearch 8 cluster (Elastic Cloud trial or self-run) with an API key |
| 8 | Jaeger / Tempo trace read-back (WP16a) | `trace_backends` mocks | A Jaeger or Grafana Tempo the TraceIQ backend can reach, and an app under test that is instrumented with OpenTelemetry |

## Rules for every check

- **Credentials never go into the repo, an issue, a PR, a commit message, the
  tracker or a screenshot.** Enter them only in the TraceIQ UI (Settings →
  Instance (Admin), or the project's own settings), where they are stored
  encrypted, or in the vendor's console. When you record a result, record the
  vendor, plan/edition, date, TraceIQ commit and outcome — never a key, token,
  routing key, webhook URL, tenant id or user email.
- Use **sandbox/trial accounts**, not production ones. Revoke the keys you
  created when you are done (each section names them).
- Use a TraceIQ instance reachable from the vendor where the vendor calls in
  (SAML ACS is a browser redirect, so a laptop works; incident webhooks need a
  public URL). Outbound checks need the backend to reach the vendor; if the
  instance enforces `OUTBOUND_ALLOWED_HOSTS`, add the vendor host first.
- A check passes only when every "Expected" line is true. Anything else is a
  failure: record what you saw, and open an issue with the steps (still no
  credentials).

## Recording results

Record each run in the tracker, `info/ROADMAP_EXECUTION_2026-10.md`, in a
"Real-service verification" table under the H5 row (add the table the first
time), one row per check:

```
| Check | Vendor / edition | Date | TraceIQ commit | Result | Notes / issue |
|---|---|---|---|---|---|
| 2 PagerDuty | PagerDuty Free | 2026-11-02 | abc1234 | pass | — |
```

`Result` is `pass`, `fail` (link the issue) or `blocked` (say what was missing).

---

## 1. SAML 2.0 against a real IdP

**Owner provides:** an IdP tenant where you can create an enterprise
application — Entra ID (free tier is enough), Okta developer org, or a
Keycloak realm — and two test users, one in a group you will map.

**Setup**
1. In TraceIQ: Settings → Instance (Admin) → Single sign-on (SAML 2.0). Set
   `SAML_SP_ACS_URL` to `https://<your-traceiq>/api/auth/saml/acs`. Save.
2. In the IdP: create a SAML app from TraceIQ's metadata at
   `https://<your-traceiq>/api/auth/saml/metadata` (entity id + ACS URL), and
   release `email`, `name` and a groups attribute.
3. Back in TraceIQ: paste the IdP's metadata URL into `SAML_IDP_METADATA_URL`.
   Save — the form validates the metadata and refuses a bad URL.
4. Federation group: `FEDERATED_PROVISIONING_MODE=workspace`, pick a
   workspace, and set `FEDERATED_GROUP_ROLE_MAP` to map your test group to
   `Workspace Admin` (see `docs/ENTERPRISE_AUTH.md`).

**Steps and expected results**
1. Sign out; on `/login` click the SAML button; sign in at the IdP as the
   grouped user. → You land in TraceIQ signed in, in the configured workspace,
   as Workspace Admin. No tenant of your own was created.
2. Sign in as the ungrouped user. → Lands as `FEDERATED_DEFAULT_ROLE`.
3. Remove the grouped user from the group in the IdP; sign in again. → The
   admin role is gone (group maps re-apply on every login).
4. Replay: in the browser devtools copy the `SAMLResponse` form post from
   step 1 and re-POST it to the ACS URL (e.g. with curl). → Refused (assertion
   already used).
5. IdP-initiated sign-in from the IdP's app launcher with
   `SAML_ALLOW_IDP_INITIATED` off. → Refused.
6. Deactivate the user in TraceIQ (Users), sign in via SAML. → Refused, no
   session.
7. Workspace → Audit log. → A login row per successful sign-in, failure rows
   for steps 4–6.

**Clean up:** delete the IdP app and test users; clear the SAML settings if
the instance should not keep SSO.

## 2. PagerDuty

**Owner provides:** a PagerDuty account (free plan works) with a service that
has an **Events API v2** integration; its 32-character integration (routing)
key.

**Setup:** in a TraceIQ project, Watch → On-call → Paging channels → add a
PagerDuty channel with the routing key, events "monitor down/recovery" and
"run failure". Create a monitor (Watch → Monitors) on a page you control.

**Steps and expected results**
1. Paging channel → "Send test". → An incident appears on the PagerDuty
   service within a minute; TraceIQ's paging event log shows it as `sent`.
2. Make the monitored page fail (stop it, or point the monitor at a path that
   404s with an assertion on content). → One incident opens (not one per
   check); the paging event log shows `trigger`.
3. Restore the page. → The incident resolves automatically; the log shows
   `resolve` with the same dedup key.
4. Add a maintenance window covering now and repeat step 2. → No incident; the
   log records the event as suppressed.
5. Start a run of a failing suite by hand (UI). → No page (only schedule / CI /
   webhook runs page). Trigger it from a schedule → pages.

**Clean up:** delete the paging channel; delete the integration in PagerDuty.

## 3. Opsgenie

**Owner provides:** an Opsgenie account and an **API** integration key; know
whether the account is US or EU.

**Setup and steps:** as for PagerDuty, with an Opsgenie channel and the right
region. Expected: an alert opens on test/trigger, closes on recovery (alias =
dedup key), is suppressed during a maintenance window, and an EU account works
only with the EU region selected (the US endpoint refuses the key).

**Clean up:** delete the channel and the integration key.

## 4. Jira (requirements import, ticket creation)

**Owner provides:** a Jira Cloud site (free plan works), a project with a few
issues that have acceptance criteria in their description (one in Gherkin, one
as a bullet list), and an API token for a user who can browse and create
issues there.

**Setup:** Settings → Issue trackers → add Jira (site URL, email, API token,
project key) in the workspace that owns the TraceIQ project.

**Steps and expected results**
1. Releases → Requirements → Import: search by JQL (e.g. `project = KEY`). →
   The issues are listed with their summaries; importing stores them with the
   acceptance criteria parsed (each criterion shown, ambiguous ones flagged).
2. "Generate cases" on an imported requirement. → One CREATE proposal per
   criterion in Today → Proposals (or skeleton proposals marked
   `needs_authoring` when no LLM is configured).
3. Edit the issue in Jira, then "Sync". → The change shows in TraceIQ.
4. From a failed run, create a ticket. → An issue appears in the Jira project
   with the run link; no secrets, cookies or headers in its body.
5. A tracker config from another workspace is not offered (and its id in a
   request returns 404).

**Clean up:** revoke the API token in Atlassian account settings; delete the
tracker config.

## 5. Linear (requirements import)

**Owner provides:** a Linear workspace with a team and a few issues with
acceptance criteria; a personal API key.

**Setup:** Settings → Issue trackers → add Linear.

**Steps and expected results:** as Jira steps 1–3 (Linear filters are passed
as GraphQL variables). Creating a ticket against a Linear config is refused
with a clear message — Linear is import-only by design.

**Clean up:** revoke the API key in Linear settings; delete the config.

## 6. Splunk HEC (SIEM streaming)

**Owner provides:** a Splunk instance (Splunk Cloud trial or Enterprise) with
an HTTP Event Collector token and an index for it; the HEC URL
(`https://<host>:8088/services/collector/event`). If it uses a private CA, its
PEM certificate.

**Setup:** Settings → Instance (Admin) → SIEM: `SIEM_ENABLED` on,
`SIEM_SINK_TYPE=splunk_hec`, `SIEM_URL`, `SIEM_TOKEN`, `SIEM_CA_CERT` if
needed. Keep `SIEM_TLS_VERIFY` on.

**Steps and expected results**
1. "Send test event". → It arrives in the HEC index within a minute.
2. Do three auditable things (change an instance setting, create and revoke
   an API key). → Each arrives as its own event within ~30 s, with
   `chain.prev_hash`/`chain.row_hash`, in the same order as Workspace → Audit
   log.
3. Disable the HEC token in Splunk, do one more auditable action, then
   re-enable it. → `GET /api/admin/siem/status` shows the backlog and retry,
   then the event arrives once (no duplicates, no gap).
4. Point `SIEM_URL` at a host with a self-signed certificate and no CA set. →
   Refused (verification stays on).

**Clean up:** disable SIEM streaming; delete the HEC token.

## 7. Elastic (SIEM streaming)

**Owner provides:** an Elasticsearch 8 cluster (Elastic Cloud trial or
self-run) and an API key allowed to write to one index.

**Setup:** SIEM → `SIEM_SINK_TYPE=elastic`, `SIEM_URL` (cluster URL),
`SIEM_TOKEN` (API key), `SIEM_ELASTIC_INDEX`.

**Steps and expected results:** as Splunk steps 1–3. In addition: after step 3
the index holds each event exactly once — the bulk `_id` is the audit row
hash, so a retried batch overwrites rather than duplicates (check the document
count against the audit log).

**Clean up:** delete the API key and the index.

## 8. Jaeger / Tempo trace read-back (WP16a)

**Owner provides:** a Jaeger (all-in-one is fine) or Grafana Tempo the
TraceIQ backend can reach — for Tempo with multi-tenancy, the tenant id — and
an application under test instrumented with OpenTelemetry that honours an
incoming `traceparent` and exports to that backend. (The monitoring overlay in
`infrastructure/monitoring/` includes a Jaeger you can use for the backend
side, but the app under test must export there too.)

**Setup**
1. Settings → Instance (Admin): `TRACE_BACKEND` = `jaeger` or `tempo`,
   `TRACE_BACKEND_URL`, and `TRACE_BACKEND_TOKEN` / `TRACE_BACKEND_TENANT` if
   the backend needs them.
2. In the project: Project settings → trace correlation → enabled. The app's
   host must be one of the project's own hosts (an environment base URL or a
   suite's allowed domains).
3. A test case that loads a page of that app and calls one of its APIs.

**Steps and expected results**
1. Run the case. → The run page's step view shows backend spans under the
   steps that made requests to the app (matched by parent-span ancestry), with
   attribute values redacted, no URLs or SQL statements.
2. Make the app return a 500 on that API and run again. → The failing step
   shows ERROR spans, and failure analysis cites `sN.trace`.
3. A step that only touches a third-party host → `no_telemetry` with reason
   "no own-host request"; with the backend stopped → "query failed", and the
   run page still loads.
4. Check the app's own logs or the trace: the `traceparent` was sent only to
   the app's hosts (not to third-party scripts) unless `cross_origin` is on.

**Clean up:** turn trace correlation off; clear the trace-backend settings.
