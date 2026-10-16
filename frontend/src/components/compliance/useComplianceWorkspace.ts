import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getProjects, getWorkspaces } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';

/** Which workspace a compliance page shows: the one picked on the page, else
 * the workspace of the project selected in the sidebar, else the first. */
export function useComplianceWorkspace() {
    const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const activeProjectId = useActiveProjectId();
    const [chosen, setChosen] = useState<number | null>(null);
    const list = workspaces.data ?? [];
    const fromProject = projects.data?.find((p) => p.id === activeProjectId)?.workspace_id;
    const fallback = list.find((w) => w.id === fromProject)?.id ?? list[0]?.id ?? null;
    const workspaceId = chosen !== null && list.some((w) => w.id === chosen) ? chosen : fallback;
    return { workspaces: list, workspaceId, setWorkspaceId: setChosen, loading: workspaces.isLoading };
}
