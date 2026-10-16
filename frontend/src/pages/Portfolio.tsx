import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { getWorkspaces, type Workspace } from '@/lib/api';
import { reportsApi, type PortfolioRow } from '@/api/reports';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { SkeletonRows } from '@/components/redesign-local/groupB/bits';
import { fmtPct, sortPortfolio, verdictOf, type PortfolioKey } from '@/components/reports/reportHelpers';

const COLUMNS: { key: PortfolioKey; label: string; numeric?: boolean }[] = [
    { key: 'project_name', label: 'Project' },
    { key: 'pass_rate_7d', label: 'Pass rate, 7 days', numeric: true },
    { key: 'pass_rate_30d', label: 'Pass rate, 30 days', numeric: true },
    { key: 'runs_7d', label: 'Runs, 7 days', numeric: true },
    { key: 'last_run', label: 'Last run' },
    { key: 'verdict', label: 'Readiness' },
    { key: 'flaky_count', label: 'Flaky tests', numeric: true },
    { key: 'open_findings', label: 'Open findings', numeric: true },
];

function selectProject(id: number) {
    // Same contract as the sidebar's project switcher.
    localStorage.setItem('activeProjectId', String(id));
    window.dispatchEvent(new Event('projectChanged'));
}

export default function Portfolio() {
    const navigate = useNavigate();
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const [sort, setSort] = useState<{ key: PortfolioKey; dir: 'asc' | 'desc' }>({ key: 'pass_rate_7d', dir: 'asc' });
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const wsId = selectedWs ?? workspaces?.[0]?.id ?? null;
    const portfolio = useQuery({ queryKey: ['portfolio', wsId], queryFn: () => reportsApi.portfolio(wsId!), enabled: !!wsId });
    const rows: PortfolioRow[] = portfolio.data ? sortPortfolio(portfolio.data.projects, sort.key, sort.dir) : [];

    const toggle = (key: PortfolioKey) => setSort((s) => (s.key === key
        ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
        : { key, dir: key === 'project_name' ? 'asc' : 'desc' }));

    return (
        <div className="flex flex-col gap-5 pb-12">
            <PageHeader
                title="Portfolio"
                subtitle="Every project you have access to in the workspace, side by side. Projects you cannot open are not listed."
                actions={workspaces && workspaces.length > 1 ? (
                    <label className="flex items-center gap-2 text-[13px] text-ink-soft">Workspace
                        <select className="h-10 rounded-full border border-input bg-white px-4 text-sm text-ink" value={wsId ?? ''}
                            onChange={(e) => setSelectedWs(parseInt(e.target.value))}>
                            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                        </select>
                    </label>
                ) : undefined}
            />
            {!wsId ? (
                <EmptyState title="No workspace">Join or create a workspace first.</EmptyState>
            ) : portfolio.isLoading ? (
                <SkeletonRows count={5} />
            ) : portfolio.isError ? (
                <EmptyState title="Could not load the portfolio">You may not be a member of this workspace.</EmptyState>
            ) : rows.length === 0 ? (
                <EmptyState title="No projects">You do not have access to any project in this workspace yet.</EmptyState>
            ) : (
                <section aria-label="Projects" className="overflow-x-auto rounded-3xl border border-border bg-white p-2 sm:p-4">
                    <table className="w-full min-w-[860px] text-sm">
                        <thead>
                            <tr className="text-left text-xs text-ink-soft">
                                {COLUMNS.map((c) => {
                                    const active = sort.key === c.key;
                                    return (
                                        <th key={c.key} scope="col" aria-sort={active ? (sort.dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                                            className={`px-2 py-2 font-medium ${c.numeric ? 'text-right' : ''}`}>
                                            <button type="button" onClick={() => toggle(c.key)}
                                                className={`inline-flex min-h-9 items-center gap-1 rounded-full px-2 hover:bg-secondary ${active ? 'font-semibold text-ink' : ''}`}>
                                                {c.label}
                                                {active && (sort.dir === 'asc' ? <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" /> : <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />)}
                                            </button>
                                        </th>
                                    );
                                })}
                                <th scope="col" className="px-2 py-2 font-medium"><span className="sr-only">Actions</span></th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((r) => {
                                const v = verdictOf(r.verdict);
                                return (
                                    <tr key={r.project_id} className="border-t border-[#F0EEE8]">
                                        <td className="px-4 py-3 font-semibold text-ink">{r.project_name}</td>
                                        <td className="px-4 py-3 text-right tabular-nums">{fmtPct(r.pass_rate_7d)}</td>
                                        <td className="px-4 py-3 text-right tabular-nums">{fmtPct(r.pass_rate_30d)}</td>
                                        <td className="px-4 py-3 text-right tabular-nums">{r.runs_7d}</td>
                                        <td className="px-4 py-3 text-ink-soft">
                                            {r.last_run?.created_at ? (
                                                <span className="flex items-center gap-2">
                                                    <Pill tone={r.last_run.status === 'passed' ? 'mint' : 'peach'}>{r.last_run.status}</Pill>
                                                    {new Date(r.last_run.created_at).toLocaleDateString()}
                                                </span>
                                            ) : 'never'}
                                        </td>
                                        <td className="px-4 py-3"><Pill tone={v.tone}>{v.label}</Pill></td>
                                        <td className="px-4 py-3 text-right tabular-nums">{r.flaky_count}</td>
                                        <td className="px-4 py-3 text-right tabular-nums">{r.open_findings}</td>
                                        <td className="px-4 py-3 text-right">
                                            <Button size="sm" variant="outline" className="h-9"
                                                onClick={() => { selectProject(r.project_id); navigate('/reports/summary'); }}>
                                                Open summary
                                            </Button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </section>
            )}
        </div>
    );
}
