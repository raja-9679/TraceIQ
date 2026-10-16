"""Instance-wide settings: DB-over-env resolution for admin-editable config.

The env file / compose environment remains the bootstrap layer and the
default. Anything an admin saves in the UI lands in the instance_settings
table and WINS over the environment ("DB wins when set"). Deleting the row
resets to the environment value. Only keys declared in REGISTRY may be stored,
which keeps bootstrap-critical config (DATABASE_URL, SECRET_KEY, Redis) out of
the database by construction — the app must be able to boot before it can
read the DB.

Consumers call effective()/effective_sync() at use time. Values are cached
for a short TTL so every process (API, celery workers, beat) converges on a
UI change within seconds without a restart — except keys marked
restart_required (storage), which are read once at process start by their
consumer and advertised as such in the UI.

Resolution never *requires* the DB: any failure (table missing during
bootstrap, DB down) falls back to the environment value, so this layer can be
imported anywhere without ordering concerns.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.config import db_url_for, settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingDef:
    key: str                 # canonical name; matches Settings attr / env var
    group: str               # UI section
    type: str = "str"        # str | int | bool | list | json
    secret: bool = False     # Fernet-encrypted at rest, masked in the API
    restart_required: bool = False
    label: str = ""
    description: str = ""


# Groups: email | notifications | ai | storage | sso | saml | ldap | federation |
#         test_inbox | siem | security_alerts | policies | worker | network | observability |
#         cost
REGISTRY: Dict[str, SettingDef] = {d.key: d for d in [
    # --- Email (SMTP) ---
    SettingDef("SMTP_HOST", "email", label="SMTP host"),
    SettingDef("SMTP_PORT", "email", type="int", label="SMTP port"),
    SettingDef("SMTP_USER", "email", label="SMTP username"),
    SettingDef("SMTP_PASSWORD", "email", secret=True, label="SMTP password"),
    SettingDef("SMTP_FROM", "email", label="From address"),
    # --- Notification channels ---
    SettingDef("NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Notifications master switch"),
    SettingDef("EMAIL_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Email notifications"),
    SettingDef("SLACK_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Slack notifications"),
    SettingDef("TEAMS_NOTIFICATIONS_ENABLED", "notifications", type="bool",
               label="Teams notifications"),
    SettingDef("NOTIFY_ON_FAILURE_ONLY", "notifications", type="bool",
               label="Notify on failures only"),
    SettingDef("SLACK_WEBHOOK_URL", "notifications", secret=True,
               label="Slack webhook URL (unused)",
               description="no longer used: it posted every project's runs to one channel. "
                           "Set a webhook per project in Project settings → Notifications"),
    SettingDef("TEAMS_WEBHOOK_URL", "notifications", secret=True,
               label="Teams webhook URL (unused)",
               description="no longer used: set a webhook per project in Project settings → Notifications"),
    # --- AI / LLM ---
    SettingDef("LLM_PROVIDER", "ai", label="Provider",
               description="anthropic | openai | gemini | ollama | openai-compatible; empty disables AI"),
    SettingDef("LLM_MODEL", "ai", label="Model"),
    SettingDef("ANTHROPIC_API_KEY", "ai", secret=True, label="Anthropic API key"),
    SettingDef("OPENAI_API_KEY", "ai", secret=True, label="OpenAI API key"),
    SettingDef("GEMINI_API_KEY", "ai", secret=True, label="Gemini API key"),
    SettingDef("OLLAMA_BASE_URL", "ai", label="Ollama base URL"),
    SettingDef("LLM_BASE_URL", "ai", label="OpenAI-compatible base URL"),
    SettingDef("LLM_API_KEY", "ai", secret=True, label="OpenAI-compatible API key"),
    SettingDef("ASSISTANT_ENABLED", "ai", type="bool", label="In-app assistant",
               description="the read-only \"Ask TraceIQ\" chat. It only reads what each "
                           "user can already see and needs a provider above"),
    # --- Storage (read at process start; UI shows a restart banner) ---
    SettingDef("MINIO_ENDPOINT", "storage", restart_required=True,
               label="S3/MinIO endpoint", description="host:port, backend-reachable"),
    SettingDef("MINIO_PUBLIC_URL", "storage", restart_required=True,
               label="S3/MinIO public URL", description="must be reachable by the BROWSER"),
    SettingDef("MINIO_ACCESS_KEY", "storage", secret=True, restart_required=True,
               label="Access key"),
    SettingDef("MINIO_SECRET_KEY", "storage", secret=True, restart_required=True,
               label="Secret key"),
    SettingDef("MINIO_BUCKET_NAME", "storage", restart_required=True,
               label="Bucket"),
    SettingDef("MINIO_USE_SSL", "storage", type="bool", restart_required=True,
               label="Use TLS to the object store",
               description="picks the scheme for a scheme-less endpoint; an endpoint "
                           "that spells out http:// or https:// wins"),
    SettingDef("MINIO_SSE_ALGORITHM", "storage", restart_required=True,
               label="Server-side encryption",
               description="AES256 or aws:kms. Blank disables it. Applies to every "
                           "upload and to baseline promotion (a server-side copy does "
                           "not inherit the source object's encryption)"),
    SettingDef("MINIO_SSE_KMS_KEY_ID", "storage", secret=True, restart_required=True,
               label="SSE-KMS key id",
               description="only used with aws:kms; blank uses the backend's default key"),
    # --- SSO (OIDC) ---
    SettingDef("OIDC_ISSUER", "sso", label="Issuer URL"),
    SettingDef("OIDC_CLIENT_ID", "sso", label="Client ID"),
    SettingDef("OIDC_CLIENT_SECRET", "sso", secret=True, label="Client secret"),
    SettingDef("OIDC_REDIRECT_URI", "sso", label="Redirect URI"),
    SettingDef("OIDC_POST_LOGIN_REDIRECT", "sso", label="Post-login redirect"),
    SettingDef("OIDC_ALLOWED_EMAIL_DOMAINS", "sso", label="Allowed email domains (optional)",
               description="comma-separated list, e.g. corp.example.com,eu.corp.example.com — "
                           "when set, only these email domains may sign in / JIT-provision via "
                           "SSO. Leave blank ONLY if the IdP is single-tenant to your org"),
    SettingDef("PASSWORD_LOGIN_DISABLED", "sso", type="bool",
               label="Disable password login (SSO only)",
               description="requires a saved OIDC config; instance admins can still "
                           "use password login as break-glass so you cannot lock yourself out"),
    SettingDef("OIDC_GROUPS_CLAIM", "sso", label="Groups claim (optional)",
               description="userinfo claim carrying the user's IdP groups — 'groups' "
                           "for Okta/Keycloak, 'roles' for some Entra setups. Only "
                           "needed if you map groups to TraceIQ roles or teams"),
    # --- SSO (SAML 2.0) ---
    SettingDef("SAML_SP_ACS_URL", "saml", label="Assertion consumer URL (this instance)",
               description="https://<your-traceiq>/api/auth/saml/acs — the exact URL "
                           "the IdP posts responses to. Also decides what Destination "
                           "a response must name; it is never read from request headers"),
    SettingDef("SAML_SP_ENTITY_ID", "saml", label="SP entity id (optional)",
               description="defaults to https://<your-traceiq>/api/auth/saml/metadata, "
                           "which is also where the IdP can download our metadata"),
    SettingDef("SAML_IDP_METADATA_URL", "saml", label="IdP metadata URL",
               description="the simplest setup: paste the IdP's federation metadata URL "
                           "and entity id, SSO endpoint and signing certificates are read "
                           "from it (re-read hourly, so certificate rotation just works)"),
    SettingDef("SAML_IDP_METADATA_XML", "saml", label="IdP metadata XML (alternative)",
               description="paste the metadata document instead, for IdPs that do not "
                           "publish a URL the server can reach"),
    SettingDef("SAML_IDP_ENTITY_ID", "saml", label="IdP entity id (manual)",
               description="only needed without metadata, or to override it"),
    SettingDef("SAML_IDP_SSO_URL", "saml", label="IdP single sign-on URL (manual)",
               description="HTTP-Redirect binding endpoint; only needed without metadata"),
    SettingDef("SAML_IDP_X509_CERT", "saml", label="IdP signing certificate (manual)",
               description="PEM or bare base64. Pinning it here overrides the "
                           "certificates from metadata — then YOU own rotation"),
    SettingDef("SAML_SP_X509_CERT", "saml", label="SP certificate (optional)",
               description="enables signed AuthnRequests and encrypted assertions. "
                           "Set together with the private key"),
    SettingDef("SAML_SP_PRIVATE_KEY", "saml", secret=True, label="SP private key (optional)",
               description="PEM. Stored encrypted. Publish the matching certificate to "
                           "the IdP via our metadata"),
    SettingDef("SAML_ATTR_EMAIL", "saml", label="Email attribute (optional)",
               description="attribute name(s) carrying the email, comma-separated. Blank "
                           "tries the usual names (email, mail, the WS-Fed emailaddress "
                           "claim, urn:oid:0.9.2342…) and finally an email-format NameID"),
    SettingDef("SAML_ATTR_NAME", "saml", label="Display-name attribute (optional)",
               description="blank tries displayName/name/cn/the WS-Fed name claim, "
                           "then givenName + sn"),
    SettingDef("SAML_ATTR_GROUPS", "saml", label="Groups attribute (optional)",
               description="attribute carrying group names for the federation maps. "
                           "Blank tries groups/memberOf/roles and the Entra groups claim. "
                           "Entra sends group OBJECT IDS unless the claim is configured "
                           "to emit names — map whatever it actually sends"),
    SettingDef("SAML_WANT_ASSERTIONS_SIGNED", "saml", type="bool",
               label="Require signed assertions",
               description="on (default): the Assertion element must be signed. Off: the "
                           "whole Response message must be signed instead. One of the two "
                           "is always required — an unsigned response is never accepted"),
    SettingDef("SAML_ALLOW_IDP_INITIATED", "saml", type="bool",
               label="Allow IdP-initiated sign-in",
               description="off by default. IdP-initiated responses have no request of "
                           "ours to match, so the only replay defence is the assertion-id "
                           "cache. Turn on only if users must start from the IdP portal"),
    SettingDef("SAML_ALLOWED_EMAIL_DOMAINS", "saml", label="Allowed email domains (optional)",
               description="comma-separated; when set, only these domains may sign in or "
                           "be provisioned through SAML"),
    SettingDef("SAML_POST_LOGIN_REDIRECT", "saml", label="Post-login redirect (optional)",
               description="where the browser lands with its session; defaults to the "
                           "OIDC post-login redirect, i.e. https://<your-traceiq>/login"),
    # --- Federated provisioning (shared by SSO and LDAP) ---
    SettingDef("FEDERATED_PROVISIONING_MODE", "federation",
               label="Where federated users land",
               description="standalone = every SSO/LDAP user gets their own tenant "
                           "(the legacy default — wrong for an organisation, it makes "
                           "one isolated tenant per employee); workspace = they join "
                           "the workspace below; deny = no just-in-time provisioning, "
                           "only invited or SCIM-provisioned accounts may sign in"),
    SettingDef("FEDERATED_WORKSPACE_ID", "federation", type="int",
               label="Target workspace id",
               description="required in 'workspace' mode; its tenant is derived from "
                           "the workspace, so the two can never disagree"),
    SettingDef("FEDERATED_DEFAULT_ROLE", "federation", label="Default workspace role",
               description="'Workspace Member' (default) or 'Workspace Admin'. Members "
                           "get no project access by design — grant it via teams, which "
                           "is what the group→team map below is for"),
    SettingDef("FEDERATED_GROUP_ROLE_MAP", "federation", label="IdP group → role",
               description="comma-separated 'group=Role' pairs, e.g. "
                           "traceiq-admins=Workspace Admin,qa=Workspace Member. "
                           "Re-evaluated on EVERY login, so removing a group in the "
                           "IdP removes the role here. Only 'Workspace Admin' and "
                           "'Workspace Member' are accepted — a directory group "
                           "cannot grant tenant administration"),
    SettingDef("FEDERATED_GROUP_TEAM_MAP", "federation", label="IdP group → team",
               description="comma-separated 'group=Team name' pairs. Teams carry "
                           "project access, so this is how federated users get to "
                           "see projects. Teams must already exist in the target "
                           "workspace. Also re-evaluated on every login"),
    SettingDef("SCIM_TOKEN", "federation", secret=True,
               label="SCIM bearer token",
               description="paste this into Okta/Entra as the provisioning API "
                           "token. Blank disables the /scim/v2 endpoints entirely "
                           "(they answer 404). SCIM provisions into the target "
                           "workspace above and needs it set. Deactivating a user "
                           "via SCIM also revokes their live refresh tokens"),
    # --- LDAP / on-prem Active Directory ---
    SettingDef("LDAP_SERVER_URL", "ldap", label="Server URL",
               description="ldaps://dc01.corp.example.com:636 — prefer ldaps; plain "
                           "ldap sends passwords in clear unless StartTLS is on"),
    SettingDef("LDAP_BIND_DN_TEMPLATE", "ldap", label="Bind DN template",
               description="{username} is substituted, e.g. {username}@corp.example.com "
                           "(AD) or uid={username},ou=people,dc=example,dc=com"),
    SettingDef("LDAP_SEARCH_BASE", "ldap", label="Search base (optional)",
               description="dc=corp,dc=example,dc=com — enables email/name lookup after bind"),
    SettingDef("LDAP_EMAIL_DOMAIN", "ldap", label="Email domain fallback",
               description="used when the directory returns no mail attribute and the "
                           "username is not an email"),
    SettingDef("LDAP_STARTTLS", "ldap", type="bool", label="StartTLS",
               description="upgrade a plain ldap:// connection to TLS before binding"),
    SettingDef("LDAP_CA_CERT", "ldap", label="CA certificate path (optional)",
               description="path to a PEM CA bundle used to validate the directory's "
                           "TLS certificate (ldaps:// or StartTLS)"),
    SettingDef("LDAP_TLS_INSECURE", "ldap", type="bool", label="Disable TLS validation (unsafe)",
               description="accept any TLS certificate from the directory — leaves the "
                           "connection open to on-path password capture; leave OFF"),
    # --- Test inbox + webhook capture (wait-for-email / wait-for-webhook steps) ---
    SettingDef("TEST_INBOX_PROVIDER", "test_inbox", label="Inbox provider",
               description="mailpit = a self-hosted SMTP catcher (compose profile "
                           "'inbox'); imap = a real mailbox using plus-addressing. "
                           "Blank disables {{inbox.address}} and the wait-for-email step"),
    SettingDef("TEST_INBOX_DOMAIN", "test_inbox", label="Mailpit address domain",
               description="generated addresses are <token>@<this domain>. Mailpit "
                           "accepts mail for any domain, so pick one that is never "
                           "delivered for real, e.g. inbox.traceiq.test"),
    SettingDef("TEST_INBOX_MAILPIT_URL", "test_inbox", label="Mailpit API URL",
               description="where the BACKEND reaches Mailpit's HTTP API, e.g. "
                           "http://mailpit:8025. The app under test sends to its "
                           "SMTP port (1025) instead"),
    SettingDef("TEST_INBOX_MAILPIT_USER", "test_inbox", label="Mailpit API user (optional)",
               description="only if Mailpit runs with --ui-auth"),
    SettingDef("TEST_INBOX_MAILPIT_PASSWORD", "test_inbox", secret=True,
               label="Mailpit API password (optional)"),
    SettingDef("TEST_INBOX_IMAP_HOST", "test_inbox", label="IMAP host",
               description="TLS only (IMAPS); plain IMAP is refused because the "
                           "mailbox password would cross the network in clear"),
    SettingDef("TEST_INBOX_IMAP_PORT", "test_inbox", type="int", label="IMAP port",
               description="default 993"),
    SettingDef("TEST_INBOX_IMAP_USER", "test_inbox", label="IMAP username"),
    SettingDef("TEST_INBOX_IMAP_PASSWORD", "test_inbox", secret=True,
               label="IMAP password / app password"),
    SettingDef("TEST_INBOX_IMAP_ADDRESS", "test_inbox", label="IMAP base address",
               description="the mailbox's own address, e.g. qa@company.com — runs "
                           "get qa+<token>@company.com, so the provider must support "
                           "plus-addressing (Gmail, Microsoft 365, Fastmail do)"),
    SettingDef("TEST_INBOX_IMAP_FOLDER", "test_inbox", label="IMAP folder",
               description="default INBOX"),
    SettingDef("TEST_HOOKS_PUBLIC_URL", "test_inbox", label="Public URL for webhook capture",
               description="the origin the APP UNDER TEST reaches TraceIQ at, e.g. "
                           "https://traceiq.example.com — {{webhook.url}} becomes "
                           "<this>/api/hooks/capture/<token>. Blank falls back to "
                           "FRONTEND_BASE_URL (nginx proxies /api)"),
    # --- SIEM streaming (WP7) ---
    SettingDef("SIEM_ENABLED", "siem", type="bool", label="Stream audit events to a SIEM",
               description="every audit row (with its hash-chain fields, so the SIEM "
                           "can verify the chain) plus security alerts. Sent from an "
                           "outbox after commit — a SIEM outage never fails a request, "
                           "events queue and retry with backoff"),
    SettingDef("SIEM_SINK_TYPE", "siem", label="Sink type",
               description="syslog | splunk_hec | elastic | webhook"),
    SettingDef("SIEM_URL", "siem", label="Destination",
               description="syslog: tcp://host:6514, tls://host:6514 or udp://host:514 "
                           "(RFC 5424; TCP uses octet-counted framing). splunk_hec: "
                           "https://splunk:8088 (the /services/collector/event path is "
                           "added). elastic: https://es:9200 (bulk API). webhook: any "
                           "HTTPS URL. Checked by the outbound-URL guard — a SIEM on a "
                           "private network needs its host in Always-allowed hostnames"),
    SettingDef("SIEM_TOKEN", "siem", secret=True, label="Token / key / signing secret",
               description="splunk_hec: the HEC token. elastic: an API key (the base64 "
                           "id:key form). webhook: the HMAC-SHA256 secret behind "
                           "X-TraceIQ-Signature. Unused for syslog"),
    SettingDef("SIEM_ELASTIC_INDEX", "siem", label="Elastic index",
               description="index (or data stream) the bulk API writes to"),
    SettingDef("SIEM_TLS_VERIFY", "siem", type="bool", label="Verify the SIEM's TLS certificate",
               description="leave on; turning it off lets anyone on the path read your "
                           "audit stream"),
    SettingDef("SIEM_CA_CERT", "siem", label="CA bundle path (optional)",
               description="PEM file inside the backend container for a private CA"),
    SettingDef("SIEM_OUTBOX_MAX", "siem", type="int", label="Outbox cap (events)",
               description="events kept while the SIEM is unreachable; past this the "
                           "OLDEST are dropped (and a warning logged) so an outage "
                           "cannot fill the database. The audit table keeps everything "
                           "regardless — replay from /audit/export"),
    # --- Security alerts (WP7) ---
    SettingDef("SECURITY_ALERTS_ENABLED", "security_alerts", type="bool",
               label="Security alerts master switch",
               description="evaluate the rules below against the audit stream and "
                           "notify the channels at the bottom. Alerts are also sent "
                           "to the SIEM as security_alert events"),
    SettingDef("ALERT_FAILED_LOGIN_BURST", "security_alerts", type="bool",
               label="Rule: failed-login burst",
               description="N failed logins for one account or from one IP address "
                           "within M minutes (password, MFA code and LDAP failures)"),
    SettingDef("ALERT_FAILED_LOGIN_THRESHOLD", "security_alerts", type="int",
               label="Failed-login burst: N failures"),
    SettingDef("ALERT_FAILED_LOGIN_WINDOW_MINUTES", "security_alerts", type="int",
               label="Failed-login burst: within M minutes"),
    SettingDef("ALERT_ADMIN_GRANTED", "security_alerts", type="bool",
               label="Rule: admin granted",
               description="instance-admin grant, or a workspace/project admin role "
                           "given to someone"),
    SettingDef("ALERT_API_KEY_CHANGES", "security_alerts", type="bool",
               label="Rule: API key created or revoked"),
    SettingDef("ALERT_MFA_WEAKENED", "security_alerts", type="bool",
               label="Rule: MFA weakened",
               description="a user disabled MFA, or MFA_REQUIRED was turned off"),
    SettingDef("ALERT_SSO_ONLY_DISABLED", "security_alerts", type="bool",
               label="Rule: SSO-only mode turned off"),
    SettingDef("ALERT_SCIM_TOKEN_CHANGED", "security_alerts", type="bool",
               label="Rule: SCIM token changed"),
    SettingDef("ALERT_SCIM_DEACTIVATION", "security_alerts", type="bool",
               label="Rule: user deactivated by SCIM"),
    SettingDef("SECURITY_ALERT_EMAILS", "security_alerts", type="list",
               label="Alert email recipients",
               description="comma-separated; uses the SMTP settings above"),
    SettingDef("SECURITY_ALERT_SLACK_WEBHOOK_URL", "security_alerts", secret=True,
               label="Alert Slack webhook URL",
               description="separate from run notifications on purpose — security "
                           "alerts belong in the security team's channel"),
    SettingDef("SECURITY_ALERT_TEAMS_WEBHOOK_URL", "security_alerts", secret=True,
               label="Alert Teams webhook URL"),
    # --- Policies ---
    SettingDef("REQUIRE_SEPARATE_APPROVER", "policies", type="bool",
               label="Agent proposals need a second person",
               description="the person who filed a proposal cannot accept it. "
                           "Turning this on here forces it for EVERY workspace — a "
                           "workspace admin cannot opt back out. Off by default "
                           "because a solo user proposing through their own agent "
                           "and accepting in the UI is a normal workflow"),
    SettingDef("AUTO_APPLY_DISABLED", "policies", type="bool",
               label="Disable auto-apply of agent proposals",
               description="kills policy-driven application instance-wide, whatever "
                           "each workspace's confidence threshold says. Auto-applied "
                           "changes have no human reviewer at any point, so a "
                           "regulated environment needs to be able to switch it off "
                           "and demonstrate that it is off"),
    # WP14b — agent session budgets (decision W3). Per X-Agent-Session-Id;
    # a workspace's Agents page overrides them. Empty / 0 = no limit.
    SettingDef("AGENT_SESSION_MAX_RUNS", "policies", type="int",
               label="Agent session: max runs",
               description="default cap on runs one agent session may start; "
                           "exceeding returns 429. 0 = no limit"),
    SettingDef("AGENT_SESSION_MAX_LLM_TOKENS", "policies", type="int",
               label="Agent session: max LLM tokens",
               description="default cap on LLM tokens spent for one agent session. 0 = no limit"),
    SettingDef("AGENT_SESSION_MAX_PROPOSALS", "policies", type="int",
               label="Agent session: max proposals",
               description="default cap on case proposals one agent session may file. 0 = no limit"),
    SettingDef("AGENT_SESSION_MAX_MINUTES", "policies", type="int",
               label="Agent session: max wall-clock minutes",
               description="minutes from a session's first budgeted action after which it "
                           "is refused. 0 = no limit"),
    SettingDef("POLICY_DENY_RETENTION_DAYS", "policies", type="int",
               label="Policy deny-log retention (days)",
               description="how long refused actions stay on the Policies page; "
                           "empty = 90, 0 = keep forever"),
    SettingDef("MFA_REQUIRED", "policies", type="bool",
               label="Require MFA for all users",
               description="password logins must enrol an authenticator app before "
                           "getting a session; SSO logins are governed by the IdP"),
    SettingDef("ALLOW_PRIVATE_NETWORK_TARGETS", "policies", type="bool",
               label="Allow tests against private networks",
               description="lets any user of this instance probe your internal network"),
    SettingDef("OUTBOUND_ALLOWED_HOSTS", "policies", type="list",
               label="Always-allowed hostnames"),
    SettingDef("RUN_RETENTION_DAYS", "policies", type="int",
               label="Run retention (days)", description="0 keeps everything forever"),
    SettingDef("AUDIT_RETENTION_DAYS", "policies", type="int",
               label="Audit-log retention (days)",
               description="0 keeps audit history forever, which is the safe default "
                           "for a compliance record. Separate from run retention on "
                           "purpose: PCI DSS Req 10 wants a year of audit history "
                           "regardless of how long you keep test artifacts"),
    SettingDef("DERIVED_RETENTION_DAYS", "policies", type="int",
               label="Derived-record retention (days)",
               description="expires TestCaseRevision snapshots, LLM usage events "
                           "and orphaned flake records. Separate from run "
                           "retention on purpose: shortening artifact retention to "
                           "save disk should not silently discard your edit "
                           "history. 0 = keep forever. The newest revision of "
                           "each case is always kept so restore still works"),
    SettingDef("ARTIFACT_ORPHAN_SWEEP_ENABLED", "policies", type="bool",
               label="Sweep orphaned artifacts",
               description="deletes MinIO objects whose owning row is gone. Only "
                           "runs/ was ever cleaned, and only via retention — "
                           "baselines/ and app binaries leaked forever. Leave the "
                           "dry-run switch below on until you have read a report"),
    SettingDef("ARTIFACT_ORPHAN_SWEEP_DRY_RUN", "policies", type="bool",
               label="Orphan sweep: report only",
               description="list what would be deleted instead of deleting it. A "
                           "sweep keyed on 'the database does not mention this' is "
                           "exactly the job to run in report mode first"),
    SettingDef("LEAK_DETECTION_ENABLED", "policies", type="bool",
               label="Leak detection on finished runs",
               description="scan each run's stored responses, console errors and step "
                           "evidence for exposed credentials (cloud keys, tokens, private "
                           "keys, high-entropy secrets) and file them as security "
                           "findings with a masked sample"),
    # --- Execution workers (WP8): delivered to workers by
    # GET /api/internal/worker-config every 60s. Blank = the worker's own env.
    SettingDef("WORKER_DEFAULT_TIMEOUT_MS", "worker", type="int",
               label="Default step timeout (ms)",
               description="Playwright default timeout for every step that does not "
                           "set its own. Blank/0 keeps the worker's DEFAULT_TIMEOUT env"),
    SettingDef("WORKER_MAX_JOB_DURATION_MS", "worker", type="int",
               label="Maximum job duration (ms)",
               description="hard wall-clock cap per job; the job is failed when it "
                           "is exceeded. Blank/0 keeps MAX_JOB_DURATION_MS"),
    SettingDef("WORKER_RUNTIME_HEAL_ENABLED", "worker", type="bool",
               label="Runtime self-heal",
               description="retry a step with an LLM-healed selector when it uniquely "
                           "matches. Needs a worker LLM provider"),
    SettingDef("WORKER_HAR_CAPTURE_ENABLED", "worker", type="bool",
               label="Capture HAR on every run",
               description="still subject to each project's capture level — HAR "
                           "needs 'full'"),
    SettingDef("WORKER_LLM_PROVIDER", "worker", label="Worker LLM provider",
               description="'inherit' = use the AI settings above for workers too; "
                           "anthropic | openai | gemini | ollama | openai-compatible "
                           "= worker-specific; blank = the worker's own env"),
    SettingDef("WORKER_LLM_MODEL", "worker", label="Worker LLM model"),
    SettingDef("WORKER_LLM_API_KEY", "worker", secret=True, label="Worker LLM API key",
               description="sent only to authenticated workers over "
                           "/api/internal/worker-config; never logged"),
    SettingDef("WORKER_LLM_BASE_URL", "worker", label="Worker LLM base URL",
               description="ollama / openai-compatible endpoint as reachable FROM "
                           "the workers"),
    SettingDef("WORKER_REGIONS", "worker", type="list",
               label="Worker regions",
               description="one per line: 'eu' = any workspace may pin runs to "
                           "region eu; 'acme-onprem=12,15' = only workspaces 12 and "
                           "15 may. A region not listed here is refused — workers "
                           "in a region can sit inside one customer's network"),
    SettingDef("EGRESS_ALLOWLIST_ENFORCED", "worker", type="bool",
               label="Enforce egress allowlists on every project",
               description="workers block browser requests to hosts outside the "
                           "project's allowlist (plus the always-allowed hosts "
                           "below) and to private addresses. Off = only projects "
                           "that opt in are enforced"),
    SettingDef("EGRESS_ALLOWED_HOSTS", "worker", type="list",
               label="Egress: always-allowed hosts",
               description="hostnames (and their subdomains) every project may "
                           "reach, e.g. a shared SSO or CDN"),
    SettingDef("PROMETHEUS_URL", "observability", label="Prometheus URL (load tests)",
               description="base URL of a Prometheus the BACKEND can reach; load-test "
                           "results evaluate their attached queries over the test "
                           "window against it. Fetched through the outbound URL guard"),
    SettingDef("PROMETHEUS_BEARER_TOKEN", "observability", secret=True,
               label="Prometheus bearer token (optional)"),
    SettingDef("PROMETHEUS_QUERY_TEMPLATES", "observability", type="list",
               label="Approved load-test queries",
               description="one per line, 'name=PromQL'. A load-test step names a "
                           "template and gets it evaluated; $project_id, "
                           "$workspace_id and $run_id are substituted, so a "
                           "template can be label-scoped to the caller's project"),
    SettingDef("PROMETHEUS_ALLOW_ADHOC_QUERIES", "observability", type="bool",
               label="Allow arbitrary load-test queries",
               description="evaluate PromQL written in the step itself. Any project "
                           "member who can author a load test can then read ANY "
                           "series this Prometheus holds — single-tenant instances "
                           "or a label-enforcing proxy only"),
    # --- Network access control (WP8) ---
    SettingDef("TRUSTED_PROXY_CIDRS", "network", type="list",
               label="Trusted reverse proxies",
               description="CIDRs of proxies whose X-Forwarded-For is believed when "
                           "resolving the client address for IP allowlists, e.g. the "
                           "nginx container's network. Empty = trust no proxy and use "
                           "the TCP peer address"),
    SettingDef("SCIM_ALLOWED_CIDRS", "network", type="list",
               label="SCIM source allowlist",
               description="CIDRs the identity provider calls SCIM from (Okta/Entra "
                           "publish theirs). Empty = any address. Refusals are audited"),
    SettingDef("REQUIRE_WORKER_MTLS", "network", type="bool",
               label="Require worker mTLS",
               description="/api/internal/* additionally requires a verified client "
                           "certificate, as reported by a trusted proxy in "
                           "X-Client-Verify: SUCCESS (nginx ssl_verify_client). The "
                           "shared worker secret is still required"),
    SettingDef("WORKER_MTLS_ALLOWED_SUBJECTS", "network", type="list",
               label="Allowed worker certificate subjects (optional)",
               description="exact subject DNs from X-Client-Cert-Subject "
                           "($ssl_client_s_dn); empty accepts any certificate the "
                           "proxy verified against its CA"),
    # --- Cost intelligence (WP12d). Empty = no money shown anywhere; the
    # rates are the operator's contract prices, never defaults we invent. ---
    SettingDef("COST_CURRENCY", "cost", label="Currency",
               description="3-letter ISO 4217 code (USD, EUR, INR …) every amount is "
                           "shown in. Leave blank to hide money everywhere"),
    SettingDef("COST_LLM_PRICES", "cost", type="json", label="LLM prices per 1M tokens",
               description="per model, as recorded in AI usage: {\"<model>\": "
                           "{\"input_per_1m\": 3.0, \"output_per_1m\": 15.0}}. Usage of a "
                           "model not listed is reported as not priced"),
    SettingDef("COST_BROWSER_MINUTE_PRICE", "cost", label="Worker price per browser-minute",
               description="what one minute of automated test execution costs you; "
                           "browser-minutes are summed case durations (an approximation "
                           "of worker time)"),
    SettingDef("MAX_CAPTURE_LEVEL", "policies", type="str",
               label="Maximum capture level",
               description="ceiling no project may exceed: none | minimal | standard | "
                           "full. 'standard' allows masked screenshots and scrubbed "
                           "logs; 'full' additionally allows video, Playwright traces "
                           "and HAR, none of which can be redacted. Set this to "
                           "'minimal' or lower for deployments near regulated data — "
                           "it is enforced at dispatch, so no project setting can "
                           "override it"),
]}

_CACHE_TTL_SECONDS = 15.0

_lock = threading.Lock()
_cache: Dict[str, str] = {}      # key -> decrypted raw string from DB
_cache_at: float = 0.0
_cache_loaded_ok: bool = False   # True once the DB has been read successfully once


def _parse(defn: SettingDef, raw: str) -> Any:
    if defn.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if defn.type == "int":
        return int(raw.strip() or 0)
    if defn.type == "list":
        return json.loads(raw) if raw.strip() else []
    if defn.type == "json":
        return json.loads(raw) if raw.strip() else None
    return raw


def serialize(defn: SettingDef, value: Any) -> str:
    if defn.type == "bool":
        return "true" if value in (True, "true", "True", 1, "1") else "false"
    if defn.type == "int":
        return str(int(value))
    if defn.type == "list":
        return json.dumps(value if isinstance(value, list) else [value])
    if defn.type == "json":
        return json.dumps(json.loads(value) if isinstance(value, str) else value)
    return str(value)


def env_default(key: str) -> Any:
    """The value the setting has with no DB override: pydantic Settings when
    declared there, plain env var otherwise (several LLM keys are read with
    os.getenv by the providers and never made it into Settings)."""
    if hasattr(settings, key):
        return getattr(settings, key)
    return os.getenv(key) or None


def _sync_engine():
    # Local import + lazy creation: this module is imported early and must not
    # drag sqlalchemy engine creation into import time.
    global _engine
    with _lock:
        try:
            return _engine
        except NameError:
            from sqlalchemy import create_engine
            _engine = create_engine(
                db_url_for(settings.DATABASE_URL, sync=True),
                pool_pre_ping=True, pool_size=2, max_overflow=2,
                # Bound the blocking refresh: effective() is called from async
                # request handlers, and without a connect timeout a slow/down DB
                # would block the whole event loop for the full libpq default.
                connect_args={"connect_timeout": 2})
            return _engine


def _load_overrides_sync() -> Dict[str, str]:
    from sqlalchemy import text
    from app.core.secrets import decrypt_secret
    out: Dict[str, str] = {}
    with _sync_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT key, value, is_secret FROM instance_settings")).fetchall()
    for key, value, is_secret in rows:
        if key not in REGISTRY:
            continue
        try:
            out[key] = decrypt_secret(value) if is_secret else value
        except Exception:
            # Falling back to the env value is deliberate — a stuck admin
            # setting must not take the instance down. But it IS silent
            # degradation: SMTP, OIDC and LLM credentials revert to whatever
            # the environment says. The usual cause is key material missing
            # from the ring; put it back in SECRETS_KEY_PREVIOUS and run
            # scripts/rotate_secrets.py.
            logger.warning(
                "instance_settings: cannot decrypt %s — falling back to the env value. "
                "Is the key that wrote it still in SECRETS_KEY / SECRETS_KEY_PREVIOUS?",
                key)
    return out


def _overrides(force: bool = False) -> Dict[str, str]:
    """Cached DB overrides.

    On a refresh failure we keep serving the LAST-KNOWN-GOOD snapshot (stale)
    rather than discarding it for an empty dict. Discarding it would silently
    revert security policies (MFA_REQUIRED / PASSWORD_LOGIN_DISABLED) and the
    active LLM provider to their env defaults during a brief DB blip — i.e.
    fail *open*. Env-only is used only until the very first successful load
    (bootstrap / pre-migration), which is the one case where there is no
    good snapshot to preserve.
    """
    global _cache, _cache_at, _cache_loaded_ok
    now = time.monotonic()
    with _lock:
        if not force and now - _cache_at < _CACHE_TTL_SECONDS:
            return _cache
    try:
        fresh = _load_overrides_sync()
    except Exception as exc:
        with _lock:
            if _cache_loaded_ok:
                # Serve stale; bump the timestamp so we don't hammer a down DB
                # every call for the whole TTL window.
                _cache_at = now
                logger.warning("instance_settings: refresh failed, serving cached values (%s)", exc)
                return _cache
        # Never loaded successfully yet — env is the truth (bootstrap/pre-migration).
        logger.debug("instance_settings: falling back to environment (%s)", exc)
        return {}
    with _lock:
        _cache, _cache_at, _cache_loaded_ok = fresh, now, True
    return fresh


def invalidate_cache() -> None:
    global _cache_at
    with _lock:
        _cache_at = 0.0


def effective(key: str) -> Any:
    """DB override if present, else the environment value. Cached ~15s."""
    defn = REGISTRY.get(key)
    if defn is None:
        return env_default(key)
    raw = _overrides().get(key)
    if raw is None:
        return env_default(key)
    try:
        return _parse(defn, raw)
    except Exception:
        logger.warning("instance_settings: bad stored value for %s; using environment", key)
        return env_default(key)


def effective_group(group: str) -> Dict[str, Any]:
    return {k: effective(k) for k, d in REGISTRY.items() if d.group == group}


def override_source(key: str) -> str:
    """'database' when an override row exists, else 'environment'."""
    return "database" if key in _overrides() else "environment"
