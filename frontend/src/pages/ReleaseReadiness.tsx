import { useState, type ReactNode } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    RefreshCw, CheckCircle2, XCircle, HelpCircle, MinusCircle, ExternalLink, GitCommit,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { qualityApi, ReadinessVerdict, QualityGateCheck } from '@/api/quality';
import { describeGateCheck, labelReason } from '@/lib/gateChecks';
import { EmptyState, Eyebrow, PageHeader, Pill } from '@/components/redesign/primitives';
import { toneClasses, type Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

const LATEST = '__latest';
const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
const when = (s: string | null) => (s ? new Date(s).toLocaleString() : '—');
const short = (c: string | null) => (c ? c.slice(0, 10) : '—');

const VERDICT: Record<ReadinessVerdict, { label: string; tone: Tone; icon: React.ComponentType<{ className?: string; 'aria-hidden'?: boolean }>; blurb: string }> = {
    ready: {
        label: 'Ready to release', icon: CheckCircle2, tone: 'mint',
        blurb: 'Every gate check passed on the evidence below.',
    },
    not_ready: {
        label: 'Not ready', icon: XCircle, tone: 'peach',
        blurb: 'At least one gate check failed.',
    },
    insufficient_data: {
        label: 'Insufficient data', icon: HelpCircle, tone: 'butter',
        blurb: 'Nothing failed, but required evidence is missing — missing evidence is never counted as a pass.',
    },
};

const OUTCOME: Record<string, { label: string; tone: Tone }> = {
    passed: { label: 'passed', tone: 'mint' },
    failed: { label: 'failed', tone: 'peach' },
    not_run: { label: 'no result', tone: 'butter' },
    passing: { label: 'passing', tone: 'mint' },
    failing: { label: 'failing', tone: 'peach' },
    partial: { label: 'partial', tone: 'butter' },
    untested: { label: 'untested', tone: 'neutral' },
};

function Outcome({ status }: { status: string }) {
    const o = OUTCOME[status] || { label: status, tone: 'neutral' as Tone };
    return <Pill tone={o.tone} className="flex-none capitalize">{o.label}</Pill>;
}

function CheckRow({ c }: { c: QualityGateCheck }) {
    const Icon = c.passed ? CheckCircle2 : c.missing_data ? MinusCircle : XCircle;
    const tone: Tone = c.passed ? 'mint' : c.missing_data ? 'butter' : 'peach';
    return (
        <li className="flex flex-col gap-1 px-5 py-3">
            <div className="flex items-center justify-between gap-3 text-sm">
                <span className="flex min-w-0 items-center gap-2">
                    <Icon className={cn('h-4 w-4 shrink-0', toneClasses[tone].ink)} aria-hidden />
                    <span className="font-semibold text-ink" title={describeGateCheck(c.name).explain || undefined}>{describeGateCheck(c.name).title}</span>
                    <span className="sr-only">{c.passed ? 'passed' : c.missing_data ? 'missing data' : 'failed'}</span>
                </span>
                <span className="text-right text-xs tabular-nums text-ink-soft">
                    <span className="text-ink">{c.actual}</span> / needs <span className="text-ink">{c.threshold}</span>
                </span>
            </div>
            {c.detail && <p className="ml-6 text-xs text-ink-soft">{c.detail}</p>}
        </li>
    );
}

/** A white card with a heading strip; the body is either a list (flush) or padded content. */
function Card({ title, sub, footer, flush = false, children }: {
    title: string; sub?: string; footer?: ReactNode; flush?: boolean; children: ReactNode;
}) {
    return (
        <section className="flex min-w-0 flex-col overflow-hidden rounded-3xl border border-border bg-white">
            <div className="flex flex-col gap-0.5 border-b border-[#F0EEE8] px-5 py-4">
                <h2 className="text-base font-semibold text-ink">{title}</h2>
                {sub && <p className="text-xs text-ink-soft">{sub}</p>}
            </div>
            <div className={cn('flex-1', !flush && 'px-5 py-4')}>{children}</div>
            {footer && <div className="border-t border-[#F0EEE8] px-5 py-3 text-sm">{footer}</div>}
        </section>
    );
}

const linkCls = 'font-medium text-ink underline decoration-ink/30 underline-offset-2 hover:decoration-ink';

export default function ReleaseReadiness() {
    const projectId = useActiveProjectId();

    // The commit pick belongs to the project it was made in: switching project
    // in the sidebar goes back to "latest run".
    const [pick, setPick] = useState<{ pid: number | null; commit: string }>({ pid: projectId, commit: LATEST });
    const commit = pick.pid === projectId ? pick.commit : LATEST;
    const setCommit = (c: string) => setPick({ pid: projectId, commit: c });
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
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Release readiness"
                badge={projectId && V ? <Pill tone={V.tone}><V.icon className="h-3.5 w-3.5" aria-hidden />{V.label}</Pill> : undefined}
                subtitle={
                    <>
                        One go/no-go per release, with the reasons: gate checks, critical cases, active quarantines and linked
                        requirements for a commit. Thresholds are set in the{' '}
                        <Link to="/quality" className={linkCls}>Quality gate policy</Link>.
                    </>
                }
                actions={projectId ? (
                    <Button variant="outline" onClick={() => q.refetch()} disabled={q.isFetching}>
                        <RefreshCw className={cn('h-4 w-4', q.isFetching && 'animate-spin')} aria-hidden /> Re-evaluate
                    </Button>
                ) : undefined}
                toolbar={projectId ? (
                    <>
                        <div className="flex items-center gap-2">
                            <label htmlFor="rr-commit" className="text-sm font-medium text-ink">Commit</label>
                            <Select value={commit} onValueChange={setCommit}>
                                <SelectTrigger id="rr-commit" className="h-10 w-[260px] rounded-full bg-white">
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
                        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); setBranch(branchDraft.trim()); setCommit(LATEST); }}>
                            <label htmlFor="rr-branch" className="text-sm font-medium text-ink">Branch (latest commit on it)</label>
                            <Input id="rr-branch" className="h-10 w-[160px] rounded-full" placeholder="e.g. main"
                                value={branchDraft} onChange={(e) => setBranchDraft(e.target.value)} />
                            <Button type="submit" variant="outline">Use branch</Button>
                        </form>
                    </>
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see whether it is ready to release.</EmptyState>
            ) : q.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : q.error ? (
                <EmptyState title="Failed to evaluate readiness">{errDetail(q.error)}</EmptyState>
            ) : r && V && (
                <>
                    <section role="status" aria-label="Verdict" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6 shadow-[0_12px_40px_rgba(28,27,34,0.06)] sm:p-7">
                        <div className="flex items-start gap-4">
                            <span className={cn('flex h-12 w-12 flex-none items-center justify-center rounded-2xl', toneClasses[V.tone].chip)} aria-hidden>
                                <V.icon className="h-6 w-6" />
                            </span>
                            <div className="flex min-w-0 flex-col gap-1">
                                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{V.label}</h2>
                                <div className="flex flex-wrap items-center gap-1.5 text-sm text-ink-soft">
                                    <GitCommit className="h-3.5 w-3.5" aria-hidden />
                                    {r.git_commit ? <span className="font-mono text-ink [overflow-wrap:anywhere]">{r.git_commit}</span> : <span>no commit</span>}
                                    {r.git_branch && <span>· {r.git_branch}</span>}
                                    <span>· {r.runs.length} run(s) evaluated</span>
                                    {r.scope !== 'commit' && r.scope !== 'none' && <span>· chosen from the latest {r.scope === 'branch' ? 'run on the branch' : 'run'}</span>}
                                </div>
                            </div>
                        </div>
                        <p className="text-[15px] text-ink">{V.blurb}</p>
                        {r.reasons.length > 0 && (
                            <div className="flex flex-col gap-2 rounded-2xl border border-border p-4">
                                <Eyebrow>Reasons</Eyebrow>
                                <ul className="ml-5 list-disc space-y-1 text-sm text-ink">
                                    {r.reasons.map((x, i) => <li key={i}>{labelReason(x)}</li>)}
                                </ul>
                            </div>
                        )}
                    </section>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <Card title="Gate checks" sub="Each check shows what was measured against what the policy requires." flush>
                            <ul className="flex flex-col divide-y divide-[#F0EEE8]">
                                {r.gate.checks.map((c) => <CheckRow key={c.name} c={c} />)}
                            </ul>
                        </Card>

                        <Card title="Runs considered" sub={r.git_commit ? `Every finished run on ${short(r.git_commit)}.` : undefined} flush>
                            {r.runs.length === 0 ? (
                                <p className={cn('px-5 py-4 text-sm font-medium', toneClasses.butter.ink)}>
                                    No finished run{r.git_commit ? ' for this commit' : ''}.
                                </p>
                            ) : (
                                <ul className="flex flex-col divide-y divide-[#F0EEE8] text-sm">
                                    {r.runs.map((run) => (
                                        <li key={run.id}>
                                            <Link to={`/runs/${run.id}`} className="flex min-h-12 items-center justify-between gap-3 px-5 py-2.5 hover:bg-background">
                                                <span className="flex min-w-0 flex-col">
                                                    <span className="truncate font-medium text-ink">
                                                        #{run.id} {run.suite_name || ''}
                                                    </span>
                                                    <span className="truncate text-xs text-ink-soft">
                                                        {[run.browser, run.selection_mode && run.selection_mode !== 'full' ? run.selection_mode : null, when(run.created_at)].filter(Boolean).join(' · ')}
                                                    </span>
                                                </span>
                                                <span className="flex shrink-0 items-center gap-2">
                                                    <span className="font-mono text-xs tabular-nums text-ink-soft">{run.passed_tests}/{run.total_tests}</span>
                                                    <Outcome status={run.status} />
                                                </span>
                                            </Link>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                    </div>

                    <Card title="Critical cases" sub="Cases with priority “critical”. Status is on the evaluated commit; “last result” is the newest anywhere." flush>
                        {r.critical_cases.length === 0 ? (
                            <p className="px-5 py-4 text-sm text-ink-soft">No case in this project has priority “critical”. Set it on the cases a release depends on.</p>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-sm">
                                    <thead>
                                        <tr className="text-left text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                                            <th className="px-5 py-2.5">Case</th>
                                            <th className="py-2.5 pr-3">On this commit</th>
                                            <th className="py-2.5 pr-3">Last result</th>
                                            <th className="py-2.5 pr-3">When</th>
                                            <th className="py-2.5 pr-3">Commit</th>
                                            <th className="py-2.5 pr-5">Last passed</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {r.critical_cases.map((c) => (
                                            <tr key={c.case_id} className="border-t border-[#F0EEE8] align-top">
                                                <td className="px-5 py-2.5 font-medium text-ink">{c.name}</td>
                                                <td className="py-2.5 pr-3">
                                                    <Outcome status={c.status} />
                                                    {c.detail && <div className="mt-1 text-xs text-ink-soft">{c.detail}</div>}
                                                </td>
                                                <td className="py-2.5 pr-3">{c.last_status ? <Outcome status={c.last_status} /> : <span className="text-xs text-ink-soft">never ran</span>}</td>
                                                <td className="py-2.5 pr-3 text-xs text-ink-soft">{when(c.last_at)}</td>
                                                <td className="py-2.5 pr-3 font-mono text-xs text-ink-soft">{short(c.last_commit)}</td>
                                                <td className="py-2.5 pr-5 text-xs text-ink-soft">{when(c.last_passed_at)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </Card>

                    <div className="grid gap-5 lg:grid-cols-2">
                        <Card title="Active quarantines" sub="Quarantined cases are skipped at dispatch, so they are not part of this release's evidence." flush
                            footer={<Link to="/flaky-tests" className={linkCls}>Manage flaky tests</Link>}>
                            {r.quarantines.length === 0 ? (
                                <p className="px-5 py-4 text-sm text-ink-soft">No active quarantines.</p>
                            ) : (
                                <ul className="flex flex-col divide-y divide-[#F0EEE8] text-sm">
                                    {r.quarantines.map((f) => (
                                        <li key={f.flake_record_id} className="flex flex-col gap-0.5 px-5 py-3">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="font-medium text-ink">{f.case_name}</span>
                                                <span className="flex-none text-xs text-ink-soft">score {f.flake_score.toFixed(2)} · {f.source}</span>
                                            </div>
                                            <div className="text-xs text-ink-soft">
                                                Owner: {f.owner ? (f.owner.full_name || f.owner.email) : 'unassigned'} · expires {when(f.expires_at)}
                                            </div>
                                            {f.reason && <div className="text-xs text-ink-soft">{f.reason}</div>}
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>

                        <Card title="Requirements" sub="Linked requirements, judged on the same commit as the critical cases." flush
                            footer={<Link to="/traceability" className={linkCls}>Manage requirement links</Link>}>
                            {r.requirements.length === 0 ? (
                                <p className="px-5 py-4 text-sm text-ink-soft">No requirements linked to cases in this project.</p>
                            ) : (
                                <ul className="flex flex-col divide-y divide-[#F0EEE8] text-sm">
                                    {r.requirements.map((req) => (
                                        <li key={req.ref} className="flex flex-col gap-1 px-5 py-3">
                                            <div className="flex items-center justify-between gap-2">
                                                <span className="min-w-0 truncate font-medium text-ink">
                                                    {req.url ? (
                                                        <a href={req.url} target="_blank" rel="noreferrer" className={linkCls}>
                                                            {req.ref} <ExternalLink className="inline h-3 w-3" aria-hidden />
                                                        </a>
                                                    ) : req.ref}
                                                    {req.title && <span className="font-normal text-ink-soft"> — {req.title}</span>}
                                                </span>
                                                <Outcome status={req.status} />
                                            </div>
                                            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-ink-soft">
                                                {req.cases.map((c) => <span key={c.case_id}>{c.name}: {OUTCOME[c.status]?.label ?? c.status}</span>)}
                                            </div>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </Card>
                    </div>
                </>
            )}
        </div>
    );
}
