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

First SSO sign-in JIT-provisions the account through the same path as
self-registration: the user gets their own tenant and default workspace, and
any pending email invitations to existing workspaces are applied. Their
password is random and unusable — the IdP is the only way in. To have SSO
users land in *your* workspace, invite their email first; the invitation is
consumed on first login.

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
sync). First login JIT-provisions the account like SSO does. The login page
shows a **Corporate login** tab when LDAP is configured. `ldaps://` (or
StartTLS via `LDAP_STARTTLS=true`) is strongly recommended — plain `ldap://`
sends the password in clear on the wire.
