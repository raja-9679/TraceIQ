import { workspaceRoleLabel } from './roles';

// POST /admin/users/{id}/assignments reports one outcome per workspace:
// {workspace_id, status: "updated"|"unchanged"|..., role, previous_role} or
// {workspace_id?, status: "error", message}. Older backends returned only a
// message, so anything without a list yields [].
export interface AssignmentOutcome {
    workspace_id?: number;
    status: string;
    role?: string | null;
    previous_role?: string | null;
    message?: string;
}

export function assignmentOutcomes(data: unknown): AssignmentOutcome[] {
    const list = Array.isArray(data) ? data
        : data && typeof data === 'object'
            ? ((data as Record<string, unknown>).results ?? (data as Record<string, unknown>).assignments)
            : null;
    return Array.isArray(list) ? list.filter((x): x is AssignmentOutcome => !!x && typeof x === 'object' && 'status' in x) : [];
}

export function describeOutcome(o: AssignmentOutcome): string {
    if (o.status === 'error') return o.message || 'Failed';
    if (o.status === 'unchanged') return `Already ${workspaceRoleLabel(o.role)} — unchanged`;
    if (o.status === 'updated') {
        return o.previous_role
            ? `${workspaceRoleLabel(o.previous_role)} → ${workspaceRoleLabel(o.role)}`
            : `Added as ${workspaceRoleLabel(o.role)}`;
    }
    return o.message || (o.role ? `${o.status}: ${workspaceRoleLabel(o.role)}` : o.status);
}
