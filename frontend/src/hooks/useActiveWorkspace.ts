import { useQuery } from '@tanstack/react-query';
import { getProjects } from '@/lib/api';
import { useActiveProjectId } from './useActiveProject';

/**
 * The workspace of the project picked in the sidebar — the sensible default
 * for workspace-level pages (costs, access reviews, trackers…). Defaulting to
 * the first workspace in the list instead showed another workspace's data to
 * anyone who belongs to several.
 */
export function useActiveWorkspaceId(): number | null {
    const projectId = useActiveProjectId();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    return projects.data?.find((p) => p.id === projectId)?.workspace_id ?? null;
}
