import { useQuery } from '@tanstack/react-query';
import { getProjects } from '@/lib/api';
import { usePermission } from './usePermission';

/**
 * The viewer's standing in one project, for deciding which controls to offer.
 * The server enforces every role; this only hides buttons that would 403.
 *
 * - `canEdit`: editor or admin — the `access_service` min_role="editor" gate.
 * - `can(perm)`: an RBAC permission, resolved with the project's workspace so
 *   workspace/tenant admins count (e.g. "project:create_suite", which the
 *   schedule endpoints check and only admins hold).
 */
export function useProjectAccess(projectId: number | null | undefined) {
    const { can: canRaw, isLoading } = usePermission();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = projects.data?.find((p) => p.id === projectId);
    const level = project?.access_level;
    const can = (perm: string) =>
        !!projectId && canRaw(perm, { projectId, workspaceId: project?.workspace_id });
    return {
        project,
        isLoading: isLoading || projects.isLoading,
        canEdit: level === 'editor' || level === 'admin',
        isAdmin: level === 'admin',
        can,
    };
}
