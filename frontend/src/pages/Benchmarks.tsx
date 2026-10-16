import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DetailCard, EmptyState, ListCard, ListRow, PageHeader, Pill, SplitView } from '@/components/redesign/primitives';
import { FieldRow, inputClass, textareaClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { Card, DataTable, Loading } from '@/components/journey-reports/parts';
import {
    METRIC_LABEL, errDetail, formatMetric, formatMs, healthTone, parseTargets, pct, shortDate,
} from '@/components/journey-reports/helpers';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { getTestSuites } from '@/lib/api';
import {
    createBenchmark, deleteBenchmark, getBenchmark, listBenchmarks, rerunBenchmark, type BenchmarkDetail,
} from '@/api/journeyReports';

/** One suite against several sites, side by side. */
export default function Benchmarks() {
    const projectId = useActiveProjectId();
    const [selected, setSelected] = useState<number | null>(null);
    const [creating, setCreating] = useState(false);
    return (
        <div className="flex flex-col gap-6">
            <PageHeader
                title="Benchmarks"
                subtitle="Run one suite against several sites or deployments and compare pass rates, durations and page metrics side by side."
                actions={projectId && !creating ? <Button onClick={() => setCreating(true)}>New benchmark</Button> : undefined}
            />
            {!projectId ? (
                <EmptyState title="No project selected">Select a project in the sidebar.</EmptyState>
            ) : creating ? (
                <NewBenchmark projectId={projectId} onDone={(id) => { setCreating(false); if (id) setSelected(id); }} />
            ) : (
                <GroupList projectId={projectId} selected={selected} onSelect={setSelected} />
            )}
        </div>
    );
}

function GroupList({ projectId, selected, onSelect }: { projectId: number; selected: number | null; onSelect: (id: number | null) => void }) {
    const q = useQuery({ queryKey: ['benchmarks', projectId], queryFn: () => listBenchmarks(projectId) });
    if (q.isLoading) return <Loading />;
    if (q.isError || !q.data) return <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>;
    if (!q.data.groups.length) return <EmptyState title="No benchmarks yet">Start one with New benchmark.</EmptyState>;
    const current = selected ?? q.data.groups[0].id;
    return (
        <SplitView
            list={(
                <ListCard title="Benchmarks" count={q.data.groups.length}>
                    {q.data.groups.map((g) => (
                        <ListRow key={g.id} selected={g.id === current} onSelect={() => onSelect(g.id)}
                            title={g.name}
                            meta={`${g.targets.length} sites · ${g.browser} · ${shortDate(g.created_at)}`}
                            trailing={<Pill tone={g.status === 'running' ? 'lavender' : 'neutral'}>{g.status}</Pill>} />
                    ))}
                </ListCard>
            )}
            detail={<GroupDetail key={current} groupId={current} projectId={projectId} onGone={() => onSelect(null)} onRerun={onSelect} />}
        />
    );
}

function GroupDetail({ groupId, projectId, onGone, onRerun }: {
    groupId: number; projectId: number; onGone: () => void; onRerun: (id: number) => void;
}) {
    const qc = useQueryClient();
    const q = useQuery({
        queryKey: ['benchmark', groupId],
        queryFn: () => getBenchmark(groupId),
        // Poll while runs are still going.
        refetchInterval: (query) => ((query.state.data as BenchmarkDetail | undefined)?.status === 'running' ? 5000 : false),
    });
    const rerun = useMutation({
        mutationFn: () => rerunBenchmark(groupId),
        onSuccess: (g) => { toast.success('Benchmark started again'); qc.invalidateQueries({ queryKey: ['benchmarks', projectId] }); onRerun(g.id); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => deleteBenchmark(groupId),
        onSuccess: () => { toast.success('Benchmark deleted; its runs are kept'); qc.invalidateQueries({ queryKey: ['benchmarks', projectId] }); onGone(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    if (q.isLoading) return <Loading />;
    if (q.isError || !q.data) return <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>;
    const d = q.data;
    const m = d.matrix;
    return (
        <DetailCard label={`Benchmark ${d.name}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-xl font-bold text-ink">{d.name}</h2>
                    <p className="text-sm text-ink-soft">Suite {d.suite_name || `#${d.test_suite_id}`} · {d.browser} · {shortDate(d.created_at)}</p>
                </div>
                <div className="flex gap-2">
                    <Button size="sm" onClick={() => rerun.mutate()} disabled={rerun.isPending}>Run again</Button>
                    <Button size="sm" variant="outline" disabled={remove.isPending}
                        onClick={() => { if (window.confirm('Delete this benchmark? Its runs stay in the run list.')) remove.mutate(); }}>Delete</Button>
                </div>
            </div>
            {d.status === 'running' && <p className="text-sm text-ink-soft">Runs are still going; this view refreshes on its own.</p>}
            {d.absolute_goto_cases.length > 0 && (
                <p className={warnCalloutClass}>
                    {d.absolute_goto_cases.length} case(s) open an absolute URL, so they test the same site in every column:
                    {' '}{d.absolute_goto_cases.slice(0, 5).map((c) => c.name).join(', ')}{d.absolute_goto_cases.length > 5 && '…'}.
                    Use relative paths (<code>/cart</code>) to follow each target.
                </p>
            )}
            <DataTable label="Per-site summary" head={['Site', 'Status', 'Pass rate', 'Passed / total', 'Duration', 'Avg case', ...m.metrics.map((k) => METRIC_LABEL[k] || k)]}>
                {m.sites.map((s) => (
                    <tr key={s.index}>
                        <td className="px-2 py-2">
                            <span className="block font-medium text-ink">{s.label}</span>
                            <span className="block break-all font-mono text-[11px] text-ink-soft">{s.base_url}</span>
                        </td>
                        <td className="px-2 py-2">
                            {s.run_id ? <Link to={`/runs/${s.run_id}`}><Pill tone={healthTone(s.status)}>{s.status}</Pill></Link> : <Pill tone="neutral">{s.status}</Pill>}
                        </td>
                        <td className="px-2 py-2 font-semibold">{pct(s.pass_rate)}</td>
                        <td className="px-2 py-2">{s.passed} / {s.total}</td>
                        <td className="px-2 py-2">{formatMs(s.duration_ms)}</td>
                        <td className="px-2 py-2">{formatMs(s.avg_case_ms)}</td>
                        {m.metrics.map((k) => <td key={k} className="px-2 py-2">{formatMetric(k, s.budgets[k])}</td>)}
                    </tr>
                ))}
            </DataTable>
            <div className="flex flex-wrap gap-2 text-xs text-ink-soft">
                {m.summary.best_pass_rate && <span>Best pass rate: <strong className="text-ink">{m.summary.best_pass_rate}</strong></span>}
                {m.summary.fastest && <span>· Fastest: <strong className="text-ink">{m.summary.fastest}</strong></span>}
                <span>· {m.summary.differing_tests} test(s) differ between sites</span>
                {!m.metrics.length && <span>· No page-budget steps measured; add a check-page-budget step for LCP and friends.</span>}
            </div>
            <div>
                <h3 className="mb-2 text-sm font-semibold text-ink">Per test</h3>
                {!m.tests.length ? <p className="text-sm text-ink-soft">No results yet.</p> : (
                    <DataTable label="Per-test results by site" head={['Test', ...m.sites.map((s) => s.label)]}>
                        {m.tests.map((t) => (
                            <tr key={t.key} className={t.differs ? 'bg-background' : undefined}>
                                <td className="px-2 py-2 text-ink">{t.test_name}{t.differs && <Pill tone="butter" className="ml-2">differs</Pill>}</td>
                                {t.cells.map((c, i) => (
                                    <td key={i} className="px-2 py-2">
                                        {c ? <span className="flex items-center gap-2"><Pill tone={healthTone(c.status)}>{c.status}</Pill><span className="text-xs text-ink-soft">{formatMs(c.duration_ms)}</span></span>
                                            : <span className="text-xs text-ink-soft">—</span>}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </DataTable>
                )}
            </div>
        </DetailCard>
    );
}

function NewBenchmark({ projectId, onDone }: { projectId: number; onDone: (id: number | null) => void }) {
    const qc = useQueryClient();
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId) });
    const [name, setName] = useState('');
    const [suiteId, setSuiteId] = useState<number | null>(null);
    const [browser, setBrowser] = useState('chromium');
    const [targetsText, setTargetsText] = useState('');
    const parsed = parseTargets(targetsText);
    const create = useMutation({
        mutationFn: () => createBenchmark(projectId, { name: name.trim(), test_suite_id: suiteId as number, targets: parsed.targets, browser }),
        onSuccess: (g) => { toast.success(`Started ${g.targets.length} runs`); qc.invalidateQueries({ queryKey: ['benchmarks', projectId] }); onDone(g.id); },
        onError: (e) => toast.error(errDetail(e)),
    });
    // GET /suites returns every suite of the project (nested ones too), flat.
    const flatSuites: { id: number; name: string }[] = ((suites.data || []) as { id: number; name: string }[])
        .map((s) => ({ id: s.id, name: s.name }))
        .sort((a, b) => a.name.localeCompare(b.name));
    const canSubmit = name.trim() && suiteId && parsed.targets.length >= 2 && parsed.targets.length <= 10 && !parsed.errors.length;
    return (
        <Card title="New benchmark" description="Each site gets one run of the whole suite with its base URL swapped in. Relative gotos follow the site; absolute URLs do not. Sites must pass the project's network policy.">
            <FieldRow label="Name" htmlFor="bm-name" wide>
                <Input id="bm-name" className={inputClass} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Home page vs competitors" />
            </FieldRow>
            <FieldRow label="Suite" htmlFor="bm-suite" wide>
                <select id="bm-suite" value={suiteId ?? ''} onChange={(e) => setSuiteId(e.target.value ? Number(e.target.value) : null)}
                    className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                    <option value="">Choose a suite</option>
                    {flatSuites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
            </FieldRow>
            <FieldRow label="Browser" htmlFor="bm-browser">
                <select id="bm-browser" value={browser} onChange={(e) => setBrowser(e.target.value)}
                    className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                    {['chromium', 'firefox', 'webkit'].map((b) => <option key={b} value={b}>{b}</option>)}
                </select>
            </FieldRow>
            <div className="flex flex-col gap-1.5">
                <label htmlFor="bm-targets" className="text-sm font-medium text-ink">Sites (2 to 10), one per line</label>
                <p className="text-xs text-ink-soft">A base URL, or <code>Label | https://site.example.com</code>.</p>
                <textarea id="bm-targets" rows={5} className={textareaClass} value={targetsText} onChange={(e) => setTargetsText(e.target.value)}
                    placeholder={'Us | https://www.example.com\nRival | https://www.rival.example'} />
                {parsed.errors.map((e) => <p key={e} className="text-xs text-destructive">{e}</p>)}
                {!parsed.errors.length && <p className="text-xs text-ink-soft">{parsed.targets.length} site(s)</p>}
            </div>
            <div className="flex flex-wrap gap-3 border-t border-border pt-4">
                <Button onClick={() => create.mutate()} disabled={!canSubmit || create.isPending}>
                    {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Start benchmark
                </Button>
                <Button variant="outline" onClick={() => onDone(null)}>Cancel</Button>
            </div>
        </Card>
    );
}
