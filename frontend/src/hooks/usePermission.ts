import { useQuery } from '@tanstack/react-query';
import { getMyPermissions, UserPermissions } from '@/lib/api';

export function usePermission() {
    const { data: permissions, isLoading } = useQuery<UserPermissions>({
        queryKey: ['myPermissions'],
        queryFn: getMyPermissions,
        staleTime: 5 * 60 * 1000, // Cache for 5 minutes
    });

    const can = (action: string, context?: { workspaceId?: number; projectId?: number }) => {
        if (!permissions) return false;

        const [scope] = action.split(':');

        // 1. Check System Permissions (Global)
        // If exact match
        if (permissions.system.includes(action)) return true;
        // If wildcard (e.g. "tenant:*") - simplistic implementation
        if (permissions.system.includes(`${scope}:*`)) return true;

        // 2. Check Workspace Scope
        if (context?.workspaceId && permissions.workspace?.[context.workspaceId]) {
            const wsPerms = permissions.workspace[context.workspaceId];
            if (wsPerms.includes(action)) return true;
        }

        // 3. Check Project Scope
        if (context?.projectId && permissions.project[context.projectId]) {
            const projPerms = permissions.project[context.projectId];
            if (projPerms.includes(action)) return true;
        }

        return false;
    };

    return { can, isLoading };
}
