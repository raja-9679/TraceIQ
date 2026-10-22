import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Printer } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getWorkspaces, type Workspace } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { reportsApi } from '@/api/reports';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { Segmented, SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { SummaryView } from '@/components/reports/SummaryView';
import { ShareLinksCard } from '@/components/reports/ShareLinks';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';

type Scope = 'project' | 'workspace';
type Days = '7' | '30' | '90';

// One printable page per project or per workspace. Print uses the browser's
// own "Save as PDF"; the app shell hides itself under print CSS.
export default function ExecutiveSummary() {
    const projectId = useActiveProjectId();
    const [scope, setScope] = useState<Scope>('project');
    const [days, setDays] = useState<Days>('30');
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces, enabled: scope === 'workspace' });
    // Default to the sidebar project's workspace, not whichever is listed first.
    const activeWs = useActiveWorkspaceId();
    const wsId = selectedWs ?? activeWs ?? workspaces?.[0]?.id ?? null;
    const n = parseInt(days);

    const summary = useQuery({
        queryKey: ['exec-summary', scope, scope === 'project' ? projectId : wsId, n],
        queryFn: () => (scope === 'project' ? reportsApi.projectSummary(projectId!, n) : reportsApi.workspaceSummary(wsId!, n)),
        enabled: scope === 'project' ? !!projectId : !!wsId,
    });
    const s = summary.data;

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Executive summary"
                subtitle="Quality at a glance for people who do not live in the test results: trend, release readiness and what to watch."
                actions={<Button variant="outline" onClick={() => window.print()} disabled={!s}><Printer className="h-4 w-4" /> Print or save as PDF</Button>}
                toolbar={
                    <div className="flex flex-wrap items-center gap-3 print:hidden">
                        <Segmented label="Scope" value={scope} onChange={setScope}
                            options={[{ id: 'project', label: 'This project' }, { id: 'workspace', label: 'Whole workspace' }]} />
                        <Segmented label="Period" value={days} onChange={setDays}
                            options={[{ id: '7', label: '7 days' }, { id: '30', label: '30 days' }, { id: '90', label: '90 days' }]} />
                        {scope === 'workspace' && workspaces && workspaces.length > 1 && (
                            <label className="flex items-center gap-2 text-[13px] text-ink-soft">Workspace
                                <select className="h-10 rounded-full border border-input bg-white px-4 text-sm text-ink" value={wsId ?? ''}
                                    onChange={(e) => setSelectedWs(parseInt(e.target.value))}>
                                    {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                                </select>
                            </label>
                        )}
                    </div>
                }
            />
            {scope === 'project' && !projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its summary.</EmptyState>
            ) : summary.isLoading ? (
                <SkeletonRows count={4} />
            ) : summary.isError || !s ? (
                <EmptyState title="Could not load the summary">You may not have access to this project or workspace.</EmptyState>
            ) : (
                <>
                    <SummaryView data={{ ...s, name: (scope === 'project' ? s.project_name : s.workspace_name) ?? '' }} />
                    {scope === 'workspace' && (
                        <p className="text-xs text-ink-soft">Covers the {s.projects?.length ?? 0} project(s) you have access to in this workspace.</p>
                    )}
                    <div className="print:hidden">
                        <ShareLinksCard scope={scope} projectId={projectId} workspaceId={wsId ?? s.workspace_id} windowDays={n} />
                    </div>
                </>
            )}
        </div>
    );
}
