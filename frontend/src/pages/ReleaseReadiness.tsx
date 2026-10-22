import { useState, useEffect } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    RefreshCw, AlertCircle, Layers, CheckCircle2, XCircle, HelpCircle, MinusCircle,
    ExternalLink, GitCommit,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getProjects } from '@/lib/api';
import { qualityApi, ReadinessVerdict, QualityGateCheck } from '@/api/quality';
import { describeGateCheck, labelReason } from '@/lib/gateChecks';

const LATEST = '__latest';
const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
const when = (s: string | null) => (s ? new Date(s).toLocaleString() : '—');
const short = (c: string | null) => (c ? c.slice(0, 10) : '—');

const VERDICT: Record<ReadinessVerdict, { label: string; tone: string; icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>; blurb: string }> = {
    ready: {
        label: 'Ready to release', icon: CheckCircle2,
        tone: 'bg-emerald-50 border-emerald-200 text-emerald-800',
        blurb: 'Every gate check passed on the evidence below.',
    },
    not_ready: {
        label: 'Not ready', icon: XCircle,
        tone: 'bg-rose-50 border-rose-200 text-rose-800',
        blurb: 'At least one gate check failed.',
    },
    insufficient_data: {
        label: 'Insufficient data', icon: HelpCircle,
        tone: 'bg-amber-50 border-amber-200 text-amber-800',
        blurb: 'Nothing failed, but required evidence is missing — missing evidence is never counted as a pass.',
    },
};

const OUTCOME: Record<string, { label: string; tone: string }> = {
    passed: { label: 'passed', tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    failed: { label: 'failed', tone: 'bg-rose-50 text-rose-700 border-rose-200' },
    not_run: { label: 'no result', tone: 'bg-amber-50 text-amber-700 border-amber-200' },
    passing: { label: 'passing', tone: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
    failing: { label: 'failing', tone: 'bg-rose-50 text-rose-700 border-rose-200' },
    partial: { label: 'partial', tone: 'bg-amber-50 text-amber-700 border-amber-200' },
    untested: { label: 'untested', tone: 'bg-slate-50 text-slate-500 border-slate-200' },
};

function Outcome({ status }: { status: string }) {
    const o = OUTCOME[status] || { label: status, tone: 'bg-slate-50 text-slate-500 border-slate-200' };
    return <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${o.tone}`}>{o.label}</Badge>;
}

function CheckRow({ c }: { c: QualityGateCheck }) {
    const Icon = c.passed ? CheckCircle2 : c.missing_data ? MinusCircle : XCircle;
    const tone = c.passed ? 'text-emerald-500' : c.missing_data ? 'text-amber-500' : 'text-rose-500';
    return (
        <div className="py-2.5 border-b border-slate-100 last:border-0">
            <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex items-center gap-2 min-w-0">
                    <Icon className={`w-4 h-4 shrink-0 ${tone}`} aria-hidden />
                    <span className="font-semibold text-slate-700" title={describeGateCheck(c.name).explain || undefined}>{describeGateCheck(c.name).title}</span>
                    <span className="sr-only">{c.passed ? 'passed' : c.missing_data ? 'missing data' : 'failed'}</span>
                </span>
                <span className="text-xs text-slate-500 tabular-nums text-right">
                    {c.actual} <span className="text-slate-300">/ needs</span> {c.threshold}
                </span>
            </div>
            {c.detail && <p className="text-xs text-slate-500 mt-1 ml-6">{c.detail}</p>}
        </div>
    );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
    return (
        <section className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <h2 className="font-bold text-slate-800">{title}</h2>
            {sub && <p className="text-xs text-slate-400 mt-0.5 mb-3">{sub}</p>}
            {!sub && <div className="mb-3" />}
            {children}
        </section>
    );
}

export default function ReleaseReadiness() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId');
        return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr);
        setProjectId(id);
        setCommit(LATEST);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

    const [commit, setCommit] = useState<string>(LATEST);
    const [branchDraft, setBranchDraft] = useState('');
    const [branch, setBranch] = useState('');

    const q = useQuery({
        queryKey: ['release-readiness', projectId, commit, branch],
        queryFn: () => qualityApi.readiness(
            projectId!, commit === LATEST ? undefined : commit, commit === LATEST ? branch || undefined : undefined),
        enabled: !!projectId,
        placeholderData: keepPreviousData,
    });
    // Picker options ride on every answer; the previous answer stays up while a pick loads.
    const commits = q.data?.recent_commits ?? [];

    const r = q.data;
    const V = r ? VERDICT[r.verdict] : null;

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex flex-col lg:flex-row lg:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Release readiness</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            One go/no-go per release, with the reasons: gate checks, critical cases, active
                            quarantines and linked requirements for a commit. Thresholds are set in the{' '}
                            <Link to="/quality" className="text-indigo-600 hover:underline">Quality gate policy</Link>.
                        </p>
                    </div>
                    <div className="lg:ml-auto flex flex-wrap items-end gap-3 shrink-0">
                        <div>
                            <Label htmlFor="rr-project" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Project</Label>
                            <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                                <SelectTrigger id="rr-project" className="w-[200px] h-10 rounded-xl bg-white border-slate-200">
                                    <div className="flex items-center gap-2 min-w-0">
                                        <Layers className="w-4 h-4 text-indigo-500 shrink-0" aria-hidden />
                                        <SelectValue placeholder="Select a project" />
                                    </div>
                                </SelectTrigger>
                                <SelectContent>
                                    {(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <Label htmlFor="rr-commit" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Commit</Label>
                            <Select value={commit} onValueChange={setCommit} disabled={!projectId}>
                                <SelectTrigger id="rr-commit" className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value={LATEST}>Latest run{branch ? ` on ${branch}` : ''}</SelectItem>
                                    {commits.map((c) => (
                                        <SelectItem key={c.git_commit} value={c.git_commit}>
                                            {short(c.git_commit)}{c.git_branch ? ` · ${c.git_branch}` : ''} · {c.runs} run(s)
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); setBranch(branchDraft.trim()); setCommit(LATEST); }}>
                            <div>
                                <Label htmlFor="rr-branch" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Branch (latest commit on it)</Label>
                                <Input id="rr-branch" className="w-[160px] h-10 rounded-xl" placeholder="e.g. main"
                                    value={branchDraft} onChange={(e) => setBranchDraft(e.target.value)} disabled={!projectId} />
                            </div>
                            <Button type="submit" variant="outline" className="h-10 rounded-xl" disabled={!projectId}>Use branch</Button>
                        </form>
                        <Button variant="outline" className="h-10 rounded-xl" onClick={() => q.refetch()} disabled={!projectId || q.isFetching}>
                            <RefreshCw className={`w-4 h-4 mr-1.5 ${q.isFetching ? 'animate-spin' : ''}`} aria-hidden /> Re-evaluate
                        </Button>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" aria-hidden />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Select a project above to see whether it is ready to release.</p>
                </div>
            ) : q.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" aria-label="Loading" /></div>
            ) : q.error ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
                    <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" aria-hidden />
                    <p className="text-rose-700 font-semibold">Failed to evaluate readiness</p>
                    <p className="text-rose-600/80 text-sm">{errDetail(q.error)}</p>
                </div>
            ) : r && V && (
                <div className="space-y-6">
                    <div role="status" className={`border rounded-2xl p-5 ${V.tone}`}>
                        <div className="flex items-center gap-3">
                            <V.icon className="w-7 h-7 shrink-0" aria-hidden />
                            <div className="min-w-0">
                                <div className="text-2xl font-extrabold">{V.label}</div>
                                <div className="text-sm opacity-80 flex items-center gap-1.5 flex-wrap">
                                    <GitCommit className="w-3.5 h-3.5" aria-hidden />
                                    {r.git_commit ? <span className="font-mono">{r.git_commit}</span> : <span>no commit</span>}
                                    {r.git_branch && <span>· {r.git_branch}</span>}
                                    <span>· {r.runs.length} run(s) evaluated</span>
                                    {r.scope !== 'commit' && r.scope !== 'none' && <span>· chosen from the latest {r.scope === 'branch' ? 'run on the branch' : 'run'}</span>}
                                </div>
                            </div>
                        </div>
                        <p className="text-sm mt-3">{V.blurb}</p>
                        {r.reasons.length > 0 && (
                            <ul className="mt-2 text-sm list-disc ml-5 space-y-0.5">
                                {r.reasons.map((x, i) => <li key={i}>{labelReason(x)}</li>)}
                            </ul>
                        )}
                    </div>

                    <div className="grid lg:grid-cols-2 gap-6">
                        <Section title="Gate checks" sub="Each check shows what was measured against what the policy requires.">
                            {r.gate.checks.map((c) => <CheckRow key={c.name} c={c} />)}
                        </Section>

                        <Section title="Runs considered" sub={r.git_commit ? `Every finished run on ${short(r.git_commit)}.` : undefined}>
                            {r.runs.length === 0 ? (
                                <p className="text-sm text-amber-700">No finished run{r.git_commit ? ' for this commit' : ''}.</p>
                            ) : (
                                <ul className="space-y-1.5 text-sm">
                                    {r.runs.map((run) => (
                                        <li key={run.id} className="flex items-center justify-between gap-3">
                                            <Link to={`/runs/${run.id}`} className="text-indigo-600 hover:underline min-w-0 truncate">
                                                #{run.id} {run.suite_name || ''}{run.browser ? ` · ${run.browser}` : ''}
                                                {run.selection_mode && run.selection_mode !== 'full' ? ` · ${run.selection_mode}` : ''}
                                            </Link>
                                            <span className="flex items-center gap-2 shrink-0">
                                                <span className="text-xs text-slate-400 tabular-nums">{run.passed_tests}/{run.total_tests} · {when(run.created_at)}</span>
                                                <Outcome status={run.status} />
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Section>
                    </div>

                    <Section title="Critical cases" sub="Cases with priority “critical”. Status is on the evaluated commit; “last result” is the newest anywhere.">
                        {r.critical_cases.length === 0 ? (
                            <p className="text-sm text-slate-500">No case in this project has priority “critical”. Set it on the cases a release depends on.</p>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-left text-[10px] font-bold text-slate-400 uppercase tracking-widest">
                                            <th className="py-2 pr-3">Case</th>
                                            <th className="py-2 pr-3">On this commit</th>
                                            <th className="py-2 pr-3">Last result</th>
                                            <th className="py-2 pr-3">When</th>
                                            <th className="py-2 pr-3">Commit</th>
                                            <th className="py-2">Last passed</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {r.critical_cases.map((c) => (
                                            <tr key={c.case_id} className="border-t border-slate-100 align-top">
                                                <td className="py-2 pr-3 font-medium text-slate-700">{c.name}</td>
                                                <td className="py-2 pr-3">
                                                    <Outcome status={c.status} />
                                                    {c.detail && <div className="text-xs text-slate-400 mt-0.5">{c.detail}</div>}
                                                </td>
                                                <td className="py-2 pr-3">{c.last_status ? <Outcome status={c.last_status} /> : <span className="text-xs text-slate-400">never ran</span>}</td>
                                                <td className="py-2 pr-3 text-xs text-slate-500">{when(c.last_at)}</td>
                                                <td className="py-2 pr-3 text-xs font-mono text-slate-500">{short(c.last_commit)}</td>
                                                <td className="py-2 text-xs text-slate-500">{when(c.last_passed_at)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </Section>

                    <div className="grid lg:grid-cols-2 gap-6">
                        <Section title="Active quarantines" sub="Quarantined cases are skipped at dispatch, so they are not part of this release's evidence.">
                            {r.quarantines.length === 0 ? (
                                <p className="text-sm text-slate-500">No active quarantines.</p>
                            ) : (
                                <ul className="space-y-2 text-sm">
                                    {r.quarantines.map((f) => (
                                        <li key={f.flake_record_id} className="border border-slate-100 rounded-lg px-3 py-2">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="font-medium text-slate-700">{f.case_name}</span>
                                                <span className="text-xs text-slate-400">score {f.flake_score.toFixed(2)} · {f.source}</span>
                                            </div>
                                            <div className="text-xs text-slate-500 mt-0.5">
                                                Owner: {f.owner ? (f.owner.full_name || f.owner.email) : 'unassigned'} · expires {when(f.expires_at)}
                                            </div>
                                            {f.reason && <div className="text-xs text-slate-400 mt-0.5">{f.reason}</div>}
                                        </li>
                                    ))}
                                </ul>
                            )}
                            <Link to="/flaky-tests" className="text-xs text-indigo-600 hover:underline inline-block mt-3">Manage flaky tests</Link>
                        </Section>

                        <Section title="Requirements" sub="Linked requirements, judged on the same commit as the critical cases.">
                            {r.requirements.length === 0 ? (
                                <p className="text-sm text-slate-500">No requirements linked to cases in this project.</p>
                            ) : (
                                <ul className="space-y-2 text-sm">
                                    {r.requirements.map((req) => (
                                        <li key={req.ref} className="border border-slate-100 rounded-lg px-3 py-2">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="font-medium text-slate-700 min-w-0 truncate">
                                                    {req.url ? (
                                                        <a href={req.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline">
                                                            {req.ref} <ExternalLink className="w-3 h-3 inline" aria-hidden />
                                                        </a>
                                                    ) : req.ref}
                                                    {req.title && <span className="text-slate-400 font-normal"> — {req.title}</span>}
                                                </span>
                                                <Outcome status={req.status} />
                                            </div>
                                            <div className="text-xs text-slate-500 mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                                                {req.cases.map((c) => <span key={c.case_id}>{c.name}: {OUTCOME[c.status]?.label ?? c.status}</span>)}
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                            <Link to="/traceability" className="text-xs text-indigo-600 hover:underline inline-block mt-3">Manage requirement links</Link>
                        </Section>
                    </div>
                </div>
            )}
        </div>
    );
}
