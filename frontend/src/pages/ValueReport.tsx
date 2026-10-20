import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Printer } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getWorkspaces, type Workspace } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { valueReportApi } from '@/api/valueReport';
import { EmptyState, PageHeader } from '@/components/redesign/primitives';
import { LabelledSelect } from '@/components/reports/ReportControls';
import { ValueLinks, ValueMetricCard } from '@/components/value/ValueMetricCard';
import { defaultRange } from '@/components/poc/pocHelpers';
import { errorDetail } from '@/components/flakes/flakeHelpers';

type Scope = 'project' | 'workspace';

// Pilot value report (WP16d): what TraceIQ saved and how well its AI did,
// with every number labelled measured or estimated and its definition,
// sample size and source on the card. Printable via the browser.
export default function ValueReport() {
    const projectId = useActiveProjectId();
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const [scope, setScope] = useState<Scope>('project');
    const [wsPick, setWsPick] = useState<number | null>(null);
    const wsId = wsPick ?? workspaces?.[0]?.id ?? null;
    const initial = defaultRange();
    const [start, setStart] = useState(initial.start);
    const [end, setEnd] = useState(initial.end);
    const [applied, setApplied] = useState(initial);
    const badRange = !start || !end || start > end;

    const report = useQuery({
        queryKey: ['value-report', scope, scope === 'project' ? projectId : wsId, applied.start, applied.end],
        queryFn: () => (scope === 'project'
            ? valueReportApi.project(projectId!, applied.start, applied.end)
            : valueReportApi.workspace(wsId!, applied.start, applied.end)),
        enabled: scope === 'project' ? !!projectId : !!wsId,
    });
    const data = report.data;

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Value report"
                subtitle="Time to diagnosis, AI analysis accuracy, CI time saved and proposal outcomes for a date range. Each number says whether it is measured or an estimate, and how."
                actions={(
                    <Button variant="outline" className="rounded-full" onClick={() => window.print()}>
                        <Printer className="mr-1.5 h-4 w-4" aria-hidden /> Print
                    </Button>
                )}
                toolbar={(
                    <form
                        className="flex flex-wrap items-end gap-3"
                        onSubmit={(e) => { e.preventDefault(); if (!badRange) setApplied({ start, end }); }}
                    >
                        <LabelledSelect id="value-scope" label="Scope" value={scope} onChange={(v) => setScope(v as Scope)}>
                            <option value="project">This project</option>
                            <option value="workspace">Workspace (projects you can read)</option>
                        </LabelledSelect>
                        {scope === 'workspace' && (workspaces?.length ?? 0) > 1 && (
                            <LabelledSelect id="value-ws" label="Workspace" value={String(wsId ?? '')} onChange={(v) => setWsPick(Number(v))}>
                                {workspaces!.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                            </LabelledSelect>
                        )}
                        <label htmlFor="value-start" className="flex items-center gap-2 text-sm font-medium text-ink">
                            From
                            <Input id="value-start" type="date" className="h-10 w-40 rounded-full" value={start} onChange={(e) => setStart(e.target.value)} />
                        </label>
                        <label htmlFor="value-end" className="flex items-center gap-2 text-sm font-medium text-ink">
                            To
                            <Input id="value-end" type="date" className="h-10 w-40 rounded-full" value={end} onChange={(e) => setEnd(e.target.value)} />
                        </label>
                        <Button type="submit" className="rounded-full" disabled={badRange}>Update report</Button>
                        {badRange && <span className="text-xs text-ink">The start date must be on or before the end date.</span>}
                    </form>
                )}
            />
            {scope === 'project' && !projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar, or switch the scope to the workspace.</EmptyState>
            ) : report.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : report.error ? (
                <EmptyState title="Couldn't load the value report">{errorDetail(report.error)}</EmptyState>
            ) : data ? (
                <>
                    <p className="px-1 text-sm text-ink-soft">
                        {data.scope.kind === 'project'
                            ? <>Project <span className="font-semibold text-ink">{data.scope.project_name}</span></>
                            : <>Workspace <span className="font-semibold text-ink">{data.scope.workspace_name}</span> · {data.scope.projects.length} project{data.scope.projects.length === 1 ? '' : 's'} you can read</>}
                        {' '}· {data.window.start} to {data.window.end} · validation runs excluded
                    </p>
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(300px,1fr))] gap-4">
                        {data.metrics.map((m) => <ValueMetricCard key={m.key} m={m} />)}
                    </div>
                    <ValueLinks links={data.links} />
                </>
            ) : null}
        </div>
    );
}
