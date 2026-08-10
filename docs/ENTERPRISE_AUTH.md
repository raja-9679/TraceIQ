# Enterprise authentication

How to run TraceIQ with company-managed identity: SSO against your IdP,
mandatory MFA, SSO-only login, a bootstrapped admin account, and (for on-prem
Active Directory) LDAP.

Everything here is configured at **Settings → Instance (Admin)** by an
instance admin, or via environment variables before first boot. Instance
settings stored in the database win over the environment and apply within
~15 seconds — no restart.

## Who is the instance admin?

Two ways to hold the role:

1. **Explicit grant** — the `is_instance_admin` flag on a user. Manage it at
   `GET/POST/DELETE /api/admin/instance-admins[/{user_id}]`. You cannot revoke
   your own grant, and the fallback below is never revocable, so an instance
   always has an operator.
2. **Fallback** — any admin of the *first* tenant on the deployment (i.e. the
   first account that registered). This is how existing installs keep working.

### Bootstrap an admin at first boot

Set these in the backend environment (e.g. the community `.env`) before the
first start:

```bash
ADMIN_EMAIL=ops@yourcompany.com
ADMIN_PASSWORD=<strong password>      # used only when the account is CREATED
# optional:
ADMIN_FULL_NAME="Platform Ops"
ADMIN_ORG_NAME="YourCo QA"
```

On startup TraceIQ creates that account (own tenant + workspace, instance
admin, email pre-verified) **only if the email doesn't exist yet**. Changing
`ADMIN_PASSWORD` later does *not* rewrite the stored password — the env var is
a bootstrap, not a rolling authority. If the account already exists, startup
just heals the `is_instance_admin` flag.

This matters for SSO-only deployments: with password signup effectively
unused, the bootstrap gives you a deterministic first admin instead of
"whoever registers first".

## SSO (OIDC) — Entra ID, Okta, Google Workspace, Keycloak…

TraceIQ speaks standard OpenID Connect (authorization-code flow, discovery
document, userinfo). Configure four values in **Instance (Admin) → Single
sign-on**:

| Setting | Value |
|---|---|
| `OIDC_ISSUER` | your IdP's issuer URL (discovery at `<issuer>/.well-known/openid-configuration`) |
| `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` | from the app registration |
| `OIDC_REDIRECT_URI` | `https://<your-traceiq>/api/auth/sso/callback` |
| `OIDC_POST_LOGIN_REDIRECT` | `https://<your-traceiq>/login` |

### Microsoft Entra ID (Azure AD) step-by-step

1. Entra admin center → **App registrations → New registration**.
2. Redirect URI (type *Web*): `https://<your-traceiq>/api/auth/sso/callback`.
3. **Certificates & secrets** → new client secret; copy the *value*.
4. **API permissions**: `openid`, `profile`, `email` (delegated) are enough.
5. In TraceIQ set `OIDC_ISSUER` to
   `https://login.microsoftonline.com/<directory-tenant-id>/v2.0`,
   plus the client id/secret and the two redirect values above.

The login page shows a **Sign in with SSO** button as soon as issuer + client
id + secret are saved. ADFS 2016+ also works via its OIDC endpoints
(`https://adfs.yourco.com/adfs`). For AD **without** any OIDC-capable
front-end, use LDAP instead (below).

### What happens to SSO users

Where an IdP-authenticated user lands is a deliberate setting — see
[Federated provisioning](#federated-provisioning-sso--ldap) below. **Read it
before rolling SSO out to more than a handful of people:** the default gives
every SSO user their own tenant, which is right for one person and wrong for an
organisation.

Their password is random and unusable — the IdP is the only way in. Pending
email invitations to other workspaces are applied on first login, whichever
mode is in force.

## Federated provisioning (SSO + LDAP)

**Instance (Admin) → Federated provisioning.** Governs both OIDC and LDAP
logins.

| Setting | Meaning |
|---|---|
| `FEDERATED_PROVISIONING_MODE` | `standalone` (default) / `workspace` / `deny` |
| `FEDERATED_WORKSPACE_ID` | required in `workspace` mode — the workspace users join |
| `FEDERATED_DEFAULT_ROLE` | `Workspace Member` (default) or `Workspace Admin` |
| `FEDERATED_GROUP_ROLE_MAP` | `group=Role` pairs, comma-separated |
| `FEDERATED_GROUP_TEAM_MAP` | `group=Team name` pairs, comma-separated |
| `OIDC_GROUPS_CLAIM` | userinfo claim holding groups (default `groups`) |

**`standalone`** — every federated user gets their own tenant and is its Tenant
Admin, exactly like self-registration. This is the historical behaviour and
still the default so existing installs upgrade unchanged. It is only
appropriate for a single user or a trial: turning SSO on for a 500-person
company produces 500 isolated tenants with 500 tenant admins and no shared
project between them.

**`workspace`** — users join `FEDERATED_WORKSPACE_ID` with
`FEDERATED_DEFAULT_ROLE`, and get **no tenant of their own**. This is what you
want for an organisation. The tenant is derived from the workspace, so the two
can't disagree.

**`deny`** — no just-in-time provisioning at all. A successful IdP
authentication for an unknown email returns 403; accounts must already exist
(invited, or created out-of-band). Choose this when your directory, not
TraceIQ, decides who has an account.

### Mapping IdP groups

```
FEDERATED_GROUP_ROLE_MAP = traceiq-admins=Workspace Admin,qa=Workspace Member
FEDERATED_GROUP_TEAM_MAP = qa=QA Team,platform=Platform Team
```

- Group names are matched case-insensitively. OIDC reads them from
  `OIDC_GROUPS_CLAIM` (a JSON array or a delimited string); LDAP reads
  `memberOf` and reduces each DN to its CN.
- **Both maps are re-evaluated on every login**, not only at provisioning. Drop
  someone from `traceiq-admins` in Okta and their TraceIQ admin goes away the
  next time they sign in. Without that, a mapping would look authoritative
  while silently never revoking anything.
- With no role map configured, TraceIQ never overwrites the role — an in-app
  promotion survives. Configure a map and the IdP becomes authoritative:
  a user in no mapped group falls back to `FEDERATED_DEFAULT_ROLE`.
- Only `Workspace Admin` and `Workspace Member` may be named. A directory group
  cannot grant Tenant Admin — group creation is self-service in many
  directories, which would make it a privilege-escalation path.
- Team maps only touch teams named in the map, and only inside the target
  workspace. Teams you manage in TraceIQ are left alone.

### Why teams matter here

`Workspace Member` intentionally carries no project access. Teams are what
carry it (**Workspace → Teams → project access**), so `FEDERATED_GROUP_TEAM_MAP`
is normally how federated users come to see any projects at all. Grant the team
its project access once and every member of the mapped IdP group inherits it.

### Failure behaviour

Misconfiguration **fails closed**: `workspace` mode with a missing or deleted
workspace refuses federated logins with a 503 rather than falling back to a
tenant per user. Falling back would silently recreate the problem in a
deployment whose admin believes they configured their way out of it. The
settings screen validates the policy on save — including that the workspace
exists — so a typo surfaces on the form rather than at 9am the next morning.

A team named in the map that doesn't exist is the one exception: it logs a
warning and the user simply doesn't get that access. Refusing the login would
take the whole organisation offline because somebody renamed a team.

## SSO-only mode (disable password login)

**Instance (Admin) → Single sign-on → "Disable password login (SSO only)"**.

- Refuses to turn on until a working OIDC config is saved.
- Password login then returns 403 for everyone **except instance admins** —
  deliberate break-glass so a broken IdP config can't lock the operator out.
  The login page shows only the SSO button; admins reach the password form at
  `/login?password=1`.
- The check runs *after* password verification, so the endpoint doesn't leak
  which accounts exist.

## Mandatory MFA

**Instance (Admin) → Policies → "Require MFA for all users"** (`MFA_REQUIRED`).

Password logins by users without an enrolled authenticator get an enrollment
challenge instead of a session: scan QR → confirm a TOTP code → receive
one-time recovery codes → session issued. New signups walk through the same
step (it's offered as an optional "secure your account" step even when the
policy is off). SSO logins are not challenged — MFA for SSO users is the
IdP's job.

## LDAP / on-prem Active Directory

For AD shops without Entra/ADFS. Configure in **Instance (Admin) → LDAP**:

| Setting | Example |
|---|---|
| `LDAP_SERVER_URL` | `ldaps://dc01.corp.yourco.com:636` |
| `LDAP_BIND_DN_TEMPLATE` | `{username}@corp.yourco.com` (AD userPrincipalName) or `uid={username},ou=people,dc=yourco,dc=com` |
| `LDAP_SEARCH_BASE` | `dc=corp,dc=yourco,dc=com` (optional, enables attribute lookup) |
| `LDAP_EMAIL_DOMAIN` | fallback domain when usernames aren't emails |

Login: `POST /api/auth/ldap/login` with `username` + `password` — TraceIQ
binds *as the user* against the directory (no service account or password
sync). Provisioning follows the same
[federated provisioning](#federated-provisioning-sso--ldap) policy as SSO, and
`memberOf` supplies the group names for role/team mapping (requires
`LDAP_SEARCH_BASE`, or a DN-style bind template so the entry can be read).
The login page
shows a **Corporate login** tab when LDAP is configured. `ldaps://` (or
StartTLS via `LDAP_STARTTLS=true`) is strongly recommended — plain `ldap://`
sends the password in clear on the wire.
