import type { ApiKeySnapshot, ReviewItem, UserAccessSnapshot } from '@/api/securityOps';

export function fmtDate(iso?: string | null): string {
    return iso ? new Date(iso).toLocaleDateString() : 'never';
}

/** One-line description of what a review item grants — what the reviewer is deciding on. */
export function accessSummary(item: ReviewItem): string[] {
    if (item.subject_type === 'api_key') {
        const k = item.snapshot as ApiKeySnapshot;
        return [
            `${k.scope}${k.role ? ` · ${k.role}` : ''}${k.workspace ? ` · ${k.workspace}` : ''}`,
            `created by ${k.created_by || 'unknown'} on ${fmtDate(k.created_at)} · last used ${fmtDate(k.last_used_at)}`
            + (k.expires_at ? ` · expires ${fmtDate(k.expires_at)}` : ' · no expiry'),
        ];
    }
    const u = item.snapshot as UserAccessSnapshot;
    const lines = (u.workspaces || []).map((w) => {
        const projects = w.projects.map((p) => `${p.project}: ${p.role} (${p.via})`).join(', ');
        return `${w.workspace}: ${w.role || 'no workspace role'}`
            + (w.teams.length ? ` · teams ${w.teams.join(', ')}` : '')
            + (projects ? ` · ${projects}` : '');
    });
    const flags = [
        u.active ? null : 'DEACTIVATED',
        u.instance_admin ? 'instance admin' : null,
        u.auth_method,
        u.mfa_enabled ? 'MFA on' : 'no MFA',
        `last login ${fmtDate(u.last_login_at)}`,
    ].filter(Boolean).join(' · ');
    return [...(lines.length ? lines : ['no workspace access']), flags];
}
