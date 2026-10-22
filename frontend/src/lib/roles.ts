// Workspace roles arrive in two spellings: the RBAC role name
// ("Workspace Admin" / "Workspace Member", members/detailed) or the legacy
// UserWorkspace.role string ("admin" / "member", invitations and old rows).

export const isWorkspaceAdminRole = (role?: string | null): boolean =>
    role === 'Workspace Admin' || role === 'admin';

export function workspaceRoleLabel(role?: string | null): string {
    if (isWorkspaceAdminRole(role)) return 'Admin';
    if (role === 'Workspace Member' || role === 'member') return 'Member';
    return role || '—';
}

/** The link an invitee signs up with (Signup reads ?token=). */
export const invitationLink = (token: string): string =>
    `${window.location.origin}/signup?token=${encodeURIComponent(token)}`;
