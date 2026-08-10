"""Federated (SSO / LDAP) provisioning policy — workstream F1.

The bug this exists to fix: both the OIDC callback and the LDAP login called
`provision_standalone_user`, which creates a **new Tenant** and grants the user
**Tenant Admin** of it. Enabling SSO for a 500-person company therefore
produced 500 isolated, self-administered tenants — every user their own island,
nobody able to see a shared project, and 500 tenant admins. That is a
functional blocker, not a compliance nicety.

The fix is a policy layer: an operator declares where federated users land
(`FEDERATED_PROVISIONING_MODE`) and how IdP groups map onto TraceIQ roles and
teams. Two properties matter more than the mapping itself:

  * **Fail closed on misconfiguration.** If an operator selects `workspace`
    mode and forgets the workspace id, the login must fail loudly. Silently
    reverting to the old tenant-per-user behaviour is how you end up with the
    original bug in a deployment that believes it configured its way out of it.

  * **Re-evaluate on every login, not just at creation.** If group mapping only
    ran at JIT-creation, removing someone from the `traceiq-admins` group in
    Okta would never take their TraceIQ admin away. Create-only mapping is
    worse than none, because it looks like the IdP is authoritative when it
    isn't.

Role names are checked against an allowlist of *workspace* roles. An IdP group
must never be able to mint a Tenant Admin: group names are frequently
self-service in enterprise directories, so that would be a privilege-escalation
path handed to anyone who can create a group.

These are the pure decisions. The DB-touching provisioning itself is verified
against a real Postgres (see info/HANDOFF.md for the scratch-database recipe) —
the unit suite has no database.
"""
import pytest

from app.services import instance_settings as insvc
from app.services.federation import (
    FederationConfigError,
    WORKSPACE_ROLE_NAMES,
    group_names_from_dns,
    normalize_groups,
    parse_mapping,
    resolve_policy,
    role_for_groups,
    teams_for_groups,
    validate_proposed,
)


@pytest.fixture(autouse=True)
def fresh_cache():
    insvc.invalidate_cache()
    yield
    insvc.invalidate_cache()


def _settings(monkeypatch, **overrides):
    monkeypatch.setattr(insvc, "_load_overrides_sync", lambda: dict(overrides))
    insvc.invalidate_cache()


def _policy(**overrides):
    from app.services.federation import FederationPolicy
    base = dict(mode="workspace", workspace_id=4, default_role="Workspace Member",
                group_role_map={}, group_team_map={})
    base.update(overrides)
    return FederationPolicy(**base)


# --- Mapping syntax ---------------------------------------------------------

def test_parse_mapping_reads_comma_separated_pairs():
    assert parse_mapping("admins=Workspace Admin,qa=Workspace Member") == {
        "admins": "Workspace Admin",
        "qa": "Workspace Member",
    }


def test_parse_mapping_tolerates_newlines_and_padding():
    raw = "  traceiq-admins = Workspace Admin \n qa=Workspace Member\n\n"
    assert parse_mapping(raw) == {
        "traceiq-admins": "Workspace Admin",
        "qa": "Workspace Member",
    }


def test_parse_mapping_lowercases_group_keys_but_not_values():
    # Directories are inconsistent about case in group names; role names are
    # matched against Role.name, which is not.
    assert parse_mapping("TraceIQ-Admins=Workspace Admin") == {
        "traceiq-admins": "Workspace Admin"}


def test_parse_mapping_ignores_entries_without_a_target():
    assert parse_mapping("admins=,=Workspace Admin,qa=Workspace Member") == {
        "qa": "Workspace Member"}


def test_parse_mapping_of_blank_is_empty():
    assert parse_mapping("") == {} and parse_mapping(None) == {}


# --- Policy resolution ------------------------------------------------------

def test_default_mode_is_standalone(monkeypatch):
    # Nothing configured must keep the pre-F1 behaviour. Existing single-user
    # SSO installs upgrade without their accounts changing shape.
    _settings(monkeypatch)
    assert resolve_policy().mode == "standalone"


def test_workspace_mode_without_a_workspace_id_is_refused(monkeypatch):
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace")
    with pytest.raises(FederationConfigError):
        resolve_policy()


def test_workspace_mode_carries_the_target_and_default_role(monkeypatch):
    _settings(monkeypatch,
              FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7",
              FEDERATED_DEFAULT_ROLE="Workspace Admin")
    policy = resolve_policy()
    assert (policy.mode, policy.workspace_id, policy.default_role) == (
        "workspace", 7, "Workspace Admin")


def test_default_role_falls_back_to_workspace_member(monkeypatch):
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7")
    assert resolve_policy().default_role == "Workspace Member"


def test_unknown_default_role_is_refused(monkeypatch):
    # A typo'd role name would otherwise resolve to no role at all, leaving
    # every federated user with an account and no access — reported as "SSO is
    # broken" rather than as a settings error.
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7",
              FEDERATED_DEFAULT_ROLE="Tenant Admin")
    with pytest.raises(FederationConfigError):
        resolve_policy()


def test_unknown_mode_is_refused(monkeypatch):
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="whatever")
    with pytest.raises(FederationConfigError):
        resolve_policy()


def test_deny_mode_needs_no_workspace(monkeypatch):
    # SCIM-only / pre-provisioned deployments: no JIT of any kind.
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="deny")
    assert resolve_policy().mode == "deny"


def test_group_maps_are_resolved(monkeypatch):
    _settings(monkeypatch,
              FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7",
              FEDERATED_GROUP_ROLE_MAP="admins=Workspace Admin",
              FEDERATED_GROUP_TEAM_MAP="qa=QA Team")
    policy = resolve_policy()
    assert policy.group_role_map == {"admins": "Workspace Admin"}
    assert policy.group_team_map == {"qa": "QA Team"}


def test_group_role_map_cannot_grant_tenant_admin(monkeypatch):
    # The whole point of the allowlist: an IdP group is not a route to
    # tenant-wide administration.
    _settings(monkeypatch,
              FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7",
              FEDERATED_GROUP_ROLE_MAP="admins=Tenant Admin,qa=Workspace Member")
    with pytest.raises(FederationConfigError):
        resolve_policy()


def test_tenant_admin_is_not_an_allowed_workspace_role():
    assert "Tenant Admin" not in WORKSPACE_ROLE_NAMES


# --- Validation at save time -------------------------------------------------
#
# resolve_policy() fails closed, which means a typo takes every federated login
# down until someone notices. The admin who typed it should hear about it while
# they are still looking at the form.

def test_proposed_settings_are_validated_against_saved_ones(monkeypatch):
    # Workspace id already saved, mode arriving in this request: valid together.
    _settings(monkeypatch, FEDERATED_WORKSPACE_ID="7")
    validate_proposed({"FEDERATED_PROVISIONING_MODE": "workspace"})


def test_proposing_workspace_mode_with_no_workspace_anywhere_is_rejected(monkeypatch):
    _settings(monkeypatch)
    with pytest.raises(FederationConfigError):
        validate_proposed({"FEDERATED_PROVISIONING_MODE": "workspace"})


def test_proposed_values_win_over_saved_ones(monkeypatch):
    # Clearing the workspace id while the saved mode still needs it must fail,
    # rather than being accepted because the OLD value is still in the database.
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7")
    with pytest.raises(FederationConfigError):
        validate_proposed({"FEDERATED_WORKSPACE_ID": ""})


def test_proposing_a_bad_group_map_is_rejected(monkeypatch):
    _settings(monkeypatch, FEDERATED_PROVISIONING_MODE="workspace",
              FEDERATED_WORKSPACE_ID="7")
    with pytest.raises(FederationConfigError):
        validate_proposed({"FEDERATED_GROUP_ROLE_MAP": "admins=Tenant Admin"})


def test_validation_ignores_unrelated_keys(monkeypatch):
    _settings(monkeypatch)
    validate_proposed({"SMTP_HOST": "mail.example.com"})


# --- Group → role ------------------------------------------------------------

def test_mapped_group_selects_its_role():
    policy = _policy(group_role_map={"admins": "Workspace Admin"})
    assert role_for_groups(policy, ["admins"]) == "Workspace Admin"


def test_group_matching_ignores_case():
    policy = _policy(group_role_map={"admins": "Workspace Admin"})
    assert role_for_groups(policy, ["ADMINS"]) == "Workspace Admin"


def test_highest_privilege_wins_when_several_groups_match():
    policy = _policy(group_role_map={"admins": "Workspace Admin",
                                     "qa": "Workspace Member"})
    assert role_for_groups(policy, ["qa", "admins"]) == "Workspace Admin"


def test_unmapped_groups_get_the_default_role():
    policy = _policy(group_role_map={"admins": "Workspace Admin"})
    assert role_for_groups(policy, ["marketing"]) == "Workspace Member"


def test_no_groups_at_all_gets_the_default_role():
    policy = _policy(group_role_map={"admins": "Workspace Admin"})
    assert role_for_groups(policy, []) == "Workspace Member"


# --- Group → team ------------------------------------------------------------

def test_teams_for_groups_returns_every_match():
    policy = _policy(group_team_map={"qa": "QA Team", "devs": "Platform"})
    assert sorted(teams_for_groups(policy, ["QA", "devs"])) == ["Platform", "QA Team"]


def test_teams_for_groups_deduplicates_targets():
    policy = _policy(group_team_map={"qa": "QA Team", "qa-eu": "QA Team"})
    assert teams_for_groups(policy, ["qa", "qa-eu"]) == ["QA Team"]


def test_teams_for_groups_is_empty_without_a_map():
    assert teams_for_groups(_policy(), ["qa"]) == []


# --- Claim shapes ------------------------------------------------------------

def test_normalize_groups_accepts_a_list():
    assert normalize_groups(["qa", "admins"]) == ["qa", "admins"]


def test_normalize_groups_splits_a_delimited_string():
    # Some IdPs (and every SAML attribute) hand back one string.
    assert normalize_groups("qa admins") == ["qa", "admins"]
    assert normalize_groups("qa,admins") == ["qa", "admins"]


def test_normalize_groups_drops_blanks_and_non_strings():
    assert normalize_groups(["qa", "", None, 7, "  admins  "]) == ["qa", "admins"]


def test_normalize_groups_of_nothing_is_empty():
    assert normalize_groups(None) == [] and normalize_groups({}) == []


def test_ldap_member_of_dns_reduce_to_group_names():
    dns = ["CN=QA Team,OU=Groups,DC=corp,DC=example,DC=com",
           "cn=traceiq-admins,ou=Groups,dc=corp,dc=example,dc=com"]
    assert group_names_from_dns(dns) == ["QA Team", "traceiq-admins"]


def test_ldap_member_of_tolerates_a_bare_name():
    # Not every directory returns a DN for memberOf.
    assert group_names_from_dns(["qa"]) == ["qa"]


def test_ldap_member_of_unescapes_a_comma_in_a_group_name():
    # RFC 4514 escaping: a literal comma inside an RDN value is backslashed,
    # so naive splitting on "," truncates the group name.
    assert group_names_from_dns(
        ["CN=Payments\\, EU,OU=Groups,DC=corp"]) == ["Payments, EU"]
