import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getProjects, getWorkspaces, Workspace } from '@/lib/api';
import { costsApi } from '@/api/costs';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import CostTrend from '@/components/costs/CostTrend';
import BudgetEditor from '@/components/costs/BudgetEditor';
import { budgetTone, formatMoney, formatTokens, NOT_PRICED } from '@/components/costs/costFormat';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';
import { featureLabel } from '@/lib/llmUsage';

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[28px] font-bold leading-tight tracking-tight text-ink tabular-nums">{value}</span>
            {sub && <span className="text-xs text-ink-soft">{sub}</span>}
        </div>
    );
}

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
    return (
        <section aria-label={title} className="flex flex-col gap-4 rounded-2xl border border-border bg-white px-5 py-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
                {right}
            </div>
            {children}
        </section>
    );
}

/** What testing costs in money: AI tokens and worker time, per workspace or project. */
export default function Costs() {
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [days, setDays] = useState(30);
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const activeWs = useActiveWorkspaceId();
    const wsId = selectedWs ?? activeWs ?? workspaces?.[0]?.id ?? null;
    const projects = useQuery({ queryKey: ['projects', wsId], queryFn: () => getProjects(wsId!), enabled: !!wsId });

    const report = useQuery({
        queryKey: ['cost-report', wsId, days, projectId],
        queryFn: () => costsApi.report(wsId!, days, projectId),
        enabled: !!wsId,
        retry: false,
    });
    const r = report.data;
    const cur = r?.currency ?? null;
    const money = (v: number | null | undefined) => formatMoney(v, cur);
    const project = projects.data?.find((p) => p.id === projectId);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Costs"
                subtitle="AI tokens and worker time turned into money with your instance's price table, for the projects you can see."
                actions={
                    <>
                        <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                            <SelectTrigger aria-label="Time window" className="h-10 w-[120px] rounded-full bg-white px-4"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="7">7 days</SelectItem>
                                <SelectItem value="30">30 days</SelectItem>
                                <SelectItem value="90">90 days</SelectItem>
                                <SelectItem value="365">365 days</SelectItem>
                            </SelectContent>
                        </Select>
                        <Select value={wsId?.toString() ?? ''} onValueChange={(v) => { setSelectedWs(parseInt(v)); setProjectId(null); }}>
                            <SelectTrigger aria-label="Workspace" className="h-10 w-[200px] rounded-full bg-white px-4"><SelectValue placeholder="Workspace" /></SelectTrigger>
                            <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                        <Select value={projectId?.toString() ?? 'all'} onValueChange={(v) => setProjectId(v === 'all' ? null : parseInt(v))}>
                            <SelectTrigger aria-label="Project" className="h-10 w-[200px] rounded-full bg-white px-4"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All my projects</SelectItem>
                                {(projects.data || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </>
                }
            />

            {report.isLoading && <p className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading costs…</p>}
            {report.isError && <EmptyState title="Costs not available">You need access to at least one project in this workspace.</EmptyState>}

            {r && (
                <>
                    {!r.priced && (
                        <p className={warnCalloutClass}>
                            No currency is set, so usage is shown without money. An instance admin sets the currency and the
                            price table in <Link to="/instance-settings" className="font-medium underline underline-offset-2">Instance settings → Cost</Link>.
                        </p>
                    )}
                    {r.priced && r.total_is_partial && (
                        <p className={warnCalloutClass}>
                            Part of this usage has no price (a model missing from the price table, or no browser-minute price), so the
                            total covers the priced part only. Unpriced lines say “{NOT_PRICED}”.
                        </p>
                    )}

                    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                        <Stat label="Total" value={money(r.total)} sub={`last ${r.window_days} days`} />
                        <Stat label="AI (LLM tokens)" value={money(r.llm.amount)} sub={`${formatTokens(r.llm.tokens)} tokens`} />
                        <Stat label="Workers (browser-minutes)" value={money(r.browser.amount)} sub={`${r.browser.minutes.toLocaleString()} min${r.browser.price_per_minute != null ? ` × ${r.browser.price_per_minute}` : ''}`} />
                        {r.includes_unassigned
                            ? <Stat label="Workspace-level AI" value={money(r.unassigned_total)} sub="LLM calls not tied to a project" />
                            : <Stat label="Projects covered" value={r.projects.length} />}
                    </div>

                    <Card title={r.priced ? 'Spend per day' : 'Use per day'}>
                        <CostTrend report={r} />
                    </Card>

                    {/* r.browser.note names a database column; say the same in words. */}
                    <p className="text-xs text-ink-soft">
                        Browser-minutes are the summed run time of automated tests. They approximate worker cost: idle
                        workers, browser start-up and retry waits are not included. Manual runs and runs on a local worker
                        are left out.
                    </p>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <Card title="By model">
                            {r.llm.by_model.length === 0 ? <p className="text-sm text-ink-soft">No AI usage in this window.</p> : (
                                <Table>
                                    <TableHeader><TableRow><TableHead>Model</TableHead><TableHead className="text-right">Tokens in / out</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
                                    <TableBody>
                                        {r.llm.by_model.map((m) => (
                                            <TableRow key={`${m.provider}/${m.model}`}>
                                                <TableCell><span className="font-medium">{m.model || '(unknown)'}</span><span className="block text-xs text-ink-soft">{m.provider}</span></TableCell>
                                                <TableCell className="text-right tabular-nums">{formatTokens(m.input_tokens)} / {formatTokens(m.output_tokens)}</TableCell>
                                                <TableCell className="text-right tabular-nums">{money(m.amount)}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </Card>
                        <Card title="By feature">
                            {r.llm.by_feature.length === 0 ? <p className="text-sm text-ink-soft">No AI usage in this window.</p> : (
                                <Table>
                                    <TableHeader><TableRow><TableHead>Feature</TableHead><TableHead className="text-right">Tokens</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
                                    <TableBody>
                                        {r.llm.by_feature.map((f) => (
                                            <TableRow key={f.feature}>
                                                <TableCell>{featureLabel(f.feature)}</TableCell>
                                                <TableCell className="text-right tabular-nums">{formatTokens(f.tokens)}</TableCell>
                                                <TableCell className="text-right tabular-nums">{money(f.amount)}{f.unpriced_tokens > 0 && f.amount != null ? ' + unpriced' : ''}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            )}
                        </Card>
                    </div>

                    {!projectId && (
                        <Card title="By project">
                            <Table>
                                <TableHeader><TableRow><TableHead>Project</TableHead><TableHead className="text-right">Window</TableHead><TableHead className="text-right">This month</TableHead><TableHead className="text-right">Budget</TableHead></TableRow></TableHeader>
                                <TableBody>
                                    {r.projects.map((p) => (
                                        <TableRow key={p.project_id}>
                                            <TableCell>
                                                <button type="button" className="font-medium text-ink underline-offset-2 hover:underline" onClick={() => setProjectId(p.project_id)}>{p.name}</button>
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">{money(p.total)}</TableCell>
                                            <TableCell className="text-right tabular-nums">{money(p.month_spent)}</TableCell>
                                            <TableCell className="text-right">
                                                {p.budget == null ? <span className="text-xs text-ink-soft">none</span>
                                                    : <Pill tone={budgetTone(p.budget_pct)}>{p.budget_pct != null ? `${p.budget_pct}% of ${money(p.budget)}` : money(p.budget)}</Pill>}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </Card>
                    )}

                    <Card title="Most expensive runs">
                        {r.top_runs.length === 0 ? <p className="text-sm text-ink-soft">No runs with recorded usage in this window.</p> : (
                            <Table>
                                <TableHeader><TableRow><TableHead>Run</TableHead><TableHead className="text-right">Tokens</TableHead><TableHead className="text-right">Browser-min</TableHead><TableHead className="text-right">Cost</TableHead></TableRow></TableHeader>
                                <TableBody>
                                    {r.top_runs.map((t) => (
                                        <TableRow key={t.run_id}>
                                            <TableCell>
                                                <Link to={`/runs/${t.run_id}`} className="font-medium text-ink hover:underline">#{t.run_id} {t.suite_name}</Link>
                                                <span className="block text-xs text-ink-soft">{t.project_name}{t.created_at ? ` · ${new Date(t.created_at).toLocaleDateString()}` : ''}</span>
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">{formatTokens(t.tokens)}</TableCell>
                                            <TableCell className="text-right tabular-nums">{t.browser_minutes}</TableCell>
                                            <TableCell className="text-right tabular-nums">{money(t.total)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </Card>

                    {projectId && <BudgetEditor projectId={projectId} canEdit={project?.access_level === 'admin'} />}
                </>
            )}
        </div>
    );
}
