import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { demoApi, type DemoWorkspace } from '@/api/solution';

/** Make `projectId` the sidebar's active project (same contract as the switcher). */
export function switchToProject(projectId: number) {
    try { localStorage.setItem('activeProjectId', String(projectId)); } catch { /* per-browser nicety */ }
    window.dispatchEvent(new Event('projectChanged'));
}

/** The signed-in user's demo workspace, with create / delete actions. */
export function useDemoWorkspace() {
    const qc = useQueryClient();
    const demo = useQuery<DemoWorkspace | null>({ queryKey: ['demo-workspace'], queryFn: demoApi.get, retry: false });
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['demo-workspace'] });
        qc.invalidateQueries({ queryKey: ['workspaces'] });
        qc.invalidateQueries({ queryKey: ['projects'] });
    };
    const create = useMutation({
        mutationFn: demoApi.create,
        onSuccess: (d) => {
            refresh();
            if (d.projects[0]) switchToProject(d.projects[0].id);
        },
    });
    const remove = useMutation({ mutationFn: demoApi.remove, onSuccess: refresh });
    return { demo, create, remove };
}
