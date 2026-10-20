import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    qualityApi, type QualityGatePolicy, type CiSettings, type QualityGateCheck, type QualitySnapshot,
} from '@/api/quality';
import { DataPolicySection } from '@/components/settings/DataPolicySection';
import {
    DetailCard, EmptyState, EvidenceList, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';
import { OpenApiSpecSection } from '@/components/settings/OpenApiSpecSection';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

// The gate's check ids (backend/app/api/endpoints/quality.py), in words a
// release manager reads, with where the evidence for each one lives.
const CHECKS: Record<string, { title: string; explain: string; links: { tag: string; text: string; to: string }[] }> = {
    pass_rate: {
        title: 'Pass rate', explain: 'Share of finished runs that passed among the runs this gate evaluated. Cancelled and timed-out runs carry no verdict and are not counted.',
        links: [{ tag: 'runs', text: 'See the evaluated runs', to: '/runs' }, { tag: 'triage', text: 'Group the failures', to: '/triage' }],
    },
    security_high_findings: {
        title: 'High-severity security findings', explain: 'Open high findings across recent scans of this project.',
        links: [{ tag: 'security', text: 'Open Security', to: '/security' }],
    },
    security_medium_findings: {
        title: 'Medium-severity security findings', explain: 'Open medium findings across recent scans of this project.',
        links: [{ tag: 'security', text: 'Open Security', to: '/security' }],
    },
    monitors_up: {
        title: 'Monitors are up', explain: 'Every active production monitor must be up.',
        links: [{ tag: 'watch', text: 'Open Watch', to: '/monitors' }],
    },
    external_tests: {
        title: 'External CI tests', explain: 'The latest JUnit report pushed from CI must be green.',
        links: [],
    },
    perf_lcp: { title: 'Largest Contentful Paint budget', explain: 'Worst LCP across the evaluated runs.', links: [{ tag: 'runs', text: 'See the runs', to: '/runs' }] },
    perf_cls: { title: 'Layout shift budget', explain: 'Worst CLS across the evaluated runs.', links: [{ tag: 'runs', text: 'See the runs', to: '/runs' }] },
    perf_ttfb: { title: 'Time to first byte budget', explain: 'Worst TTFB across the evaluated runs.', links: [{ tag: 'runs', text: 'See the runs', to: '/runs' }] },
};

const describe = (c: QualityGateCheck) =>
    CHECKS[c.name] ?? { title: c.name.replace(/_/g, ' '), explain: '', links: [] };

// --- policy + CI (unchanged behaviour, restyled) -------------------------------------

function NumberRow({ label, value, onChange, step, min }: { label: string; value: number | string; onChange: (v: string) => void; step?: string; min?: number }) {
    return (
        <label className="flex min-h-11 items-center justify-between gap-3 text-sm">
            <span>{label}</span>
            <Input type="number" step={step} min={min} className="h-9 w-28 rounded-full" value={value} onChange={(e) => onChange(e.target.value)} />
        </label>
    );
}

function CheckRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
    return (
        <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 text-sm">
            <span>{label}</span>
            <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        </label>
    );
}

function PolicyForm({ projectId, initial }: { projectId: number; initial: QualityGatePolicy }) {
    const qc = useQueryClient();
    const [policy, setPolicy] = useState(initial);
    const save = useMutation({
        mutationFn: () => qualityApi.setPolicy(projectId, policy),
        onSuccess: () => {
            toast.success('Gate policy saved');
            qc.invalidateQueries({ queryKey: ['quality-gate', projectId] });
            qc.invalidateQueries({ queryKey: ['quality-policy', projectId] });
            qc.invalidateQueries({ queryKey: ['quality-policy-versions', projectId] });
        },
        onError: (e) => toast.error(`Save failed: ${errDetail(e)}`),
    });
    return (
        <section className="flex flex-col gap-1 rounded-3xl border border-border bg-white p-6">
            <h2 className="mb-2 text-base font-semibold text-ink">Gate rules</h2>
            <NumberRow label="Minimum pass rate (%)" value={policy.min_pass_rate} onChange={(v) => setPolicy({ ...policy, min_pass_rate: parseFloat(v) || 0 })} />
            <NumberRow label="Maximum high findings" value={policy.max_high_severity_findings} onChange={(v) => setPolicy({ ...policy, max_high_severity_findings: parseInt(v) || 0 })} />
            <NumberRow label="Maximum medium findings (blank = no limit)" value={policy.max_medium_severity_findings ?? ''}
                onChange={(v) => setPolicy({ ...policy, max_medium_severity_findings: v === '' ? null : parseInt(v) })} />
            <CheckRow label="Require monitors up" checked={policy.require_monitors_up} onChange={(v) => setPolicy({ ...policy, require_monitors_up: v })} />
            <CheckRow label="Require external CI tests green (ingested JUnit)" checked={policy.require_external_tests_pass ?? false}
                onChange={(v) => setPolicy({ ...policy, require_external_tests_pass: v })} />
            <div className="mt-3 border-t border-border pt-3">
                <Eyebrow>Release readiness (blank = off)</Eyebrow>
                <NumberRow label="Maximum flaky cases (quarantined or score ≥ 0.4)" min={0} value={policy.max_flaky_cases ?? ''}
                    onChange={(v) => setPolicy({ ...policy, max_flaky_cases: v === '' ? null : Math.max(0, parseInt(v) || 0) })} />
                <CheckRow label="Require every critical case passed on the commit" checked={policy.require_critical_passed ?? false}
                    onChange={(v) => setPolicy({ ...policy, require_critical_passed: v })} />
                <NumberRow label="Maximum evidence age, days (critical cases; all if none)" min={1} value={policy.max_evidence_age_days ?? ''}
                    onChange={(v) => setPolicy({ ...policy, max_evidence_age_days: v === '' ? null : Math.max(1, parseInt(v) || 1) })} />
                <CheckRow label="Require an approved release sign-off (manual testing)" checked={policy.require_manual_signoff ?? false}
                    onChange={(v) => setPolicy({ ...policy, require_manual_signoff: v })} />
                <CheckRow label="No new accessibility violations at serious or above (waived ones excluded)"
                    checked={policy.block_new_accessibility_violations ?? false}
                    onChange={(v) => setPolicy({ ...policy, block_new_accessibility_violations: v })} />
            </div>
            <div className="mt-3 border-t border-border pt-3">
                <Eyebrow>Performance budgets (0 = off)</Eyebrow>
                <NumberRow label="Maximum LCP (ms)" value={policy.max_lcp_ms ?? 0} onChange={(v) => setPolicy({ ...policy, max_lcp_ms: parseInt(v) || 0 })} />
                <NumberRow label="Maximum CLS" step="0.01" value={policy.max_cls ?? 0} onChange={(v) => setPolicy({ ...policy, max_cls: parseFloat(v) || 0 })} />
                <NumberRow label="Maximum TTFB (ms)" value={policy.max_ttfb_ms ?? 0} onChange={(v) => setPolicy({ ...policy, max_ttfb_ms: parseInt(v) || 0 })} />
            </div>
            <Button className="mt-3 self-start" onClick={() => save.mutate()} disabled={save.isPending}>Save rules</Button>
        </section>
    );
}

function CiForm({ projectId, initial }: { projectId: number; initial: CiSettings }) {
    const [ci, setCi] = useState(initial);
    const save = useMutation({
        mutationFn: () => qualityApi.setCiSettings(projectId, ci),
        onSuccess: () => toast.success('CI settings saved'),
        onError: (e) => toast.error(`Save failed: ${errDetail(e)}`),
    });
    return (
        <section className="flex flex-col gap-1 rounded-3xl border border-border bg-white p-6">
            <h2 className="text-base font-semibold text-ink">CI reporting</h2>
            <p className="mb-2 text-[13px] text-ink-soft">Opt-in, and works without git. Controls how CI consumes the gate.</p>
            <CheckRow label="Enabled" checked={ci.enabled} onChange={(v) => setCi({ ...ci, enabled: v })} />
            <CheckRow label="Enforce the gate (block on fail)" checked={ci.enforce_gate} onChange={(v) => setCi({ ...ci, enforce_gate: v })} />
            <CheckRow label="Post a PR comment (VCS consumers)" checked={ci.post_pr_comment} onChange={(v) => setCi({ ...ci, post_pr_comment: v })} />
            <Button className="mt-3 self-start" onClick={() => save.mutate()} disabled={save.isPending}>Save CI settings</Button>
        </section>
    );
}

// Every policy that has judged this gate (WP15f). A verdict names its
// version, so "why did this pass last week?" has an answer.
function PolicyHistory({ projectId }: { projectId: number }) {
    const q = useQuery({ queryKey: ['quality-policy-versions', projectId], queryFn: () => qualityApi.policyVersions(projectId) });
    const versions = q.data?.versions ?? [];
    if (!versions.length) return null;
    return (
        <section className="flex flex-col gap-2 rounded-3xl border border-border bg-white p-6">
            <h2 className="text-base font-semibold text-ink">Rule history</h2>
            <p className="text-[13px] text-ink-soft">
                Each version is kept unchanged. A gate result names the version that judged it, so an old verdict
                can be re-checked against the rules in force at the time.
            </p>
            <ul className="flex flex-col divide-y divide-border">
                {versions.slice(0, 10).map((v) => (
                    <li key={v.version} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                        <Pill tone={v.version === versions[0].version ? 'mint' : 'neutral'}>v{v.version}</Pill>
                        <span className="text-ink-soft">{v.created_at ? new Date(v.created_at).toLocaleString() : '—'}</span>
                        <span className="text-ink-soft">{v.created_by_id ? 'saved by a person' : 'recorded at evaluation'}</span>
                        <span className="ml-auto font-mono text-[11px] text-ink-soft" title={v.sha256}>{v.sha256.slice(0, 12)}</span>
                    </li>
                ))}
            </ul>
        </section>
    );
}

function PolicyPanel({ projectId }: { projectId: number }) {
    const policyQ = useQuery({ queryKey: ['quality-policy', projectId], queryFn: () => qualityApi.getPolicy(projectId) });
    const ciQ = useQuery({ queryKey: ['ci-settings', projectId], queryFn: () => qualityApi.getCiSettings(projectId) });
    return (
        <div className="flex flex-col gap-5">
            <div className="grid gap-5 lg:grid-cols-2">
                {policyQ.data && <PolicyForm key={`p-${projectId}-${policyQ.dataUpdatedAt}`} projectId={projectId} initial={policyQ.data} />}
                {ciQ.data && <CiForm key={`c-${projectId}-${ciQ.dataUpdatedAt}`} projectId={projectId} initial={ciQ.data} />}
            </div>
            {/* Data-capture policy sits with the other per-project policies: it is
                scoped to a project and reads alongside the gate it feeds. */}
            <PolicyHistory projectId={projectId} />
            <DataPolicySection projectId={projectId} />
            <OpenApiSpecSection projectId={projectId} />
        </div>
    );
}

// --- health -----------------------------------------------------------------------------

function TrendBars({ trend }: { trend: QualitySnapshot['trend'] }) {
    if (!trend.length) return <p className="text-sm text-ink-soft">No runs in this window.</p>;
    return (
        <div className="flex h-28 items-end gap-1.5" role="img"
            aria-label={`Daily pass rate: ${trend.map((t) => `${t.date} ${t.pass_rate}%`).join(', ')}`}>
            {trend.map((t) => (
                <div key={t.date} className="flex h-full flex-1 flex-col justify-end" title={`${t.date}: ${t.pass_rate}% of ${t.runs} runs`}>
                    <div
                        className={cn('w-full rounded-t-md', t.runs === 0 ? 'bg-secondary' : t.pass_rate >= 90 ? 'bg-[#8FD3B6]' : t.pass_rate >= 60 ? 'bg-[#F2C94C]' : 'bg-[#E0603A]')}
                        style={{ height: `${t.runs === 0 ? 4 : Math.max(6, t.pass_rate)}%` }}
                    />
                </div>
            ))}
        </div>
    );
}

function Tile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[26px] font-bold leading-none tracking-tight tabular-nums text-ink">{value}</span>
            {sub && <span className="text-xs text-ink-soft">{sub}</span>}
        </div>
    );
}

function Ring({ passed, total }: { passed: number; total: number }) {
    const r = 30, c = 2 * Math.PI * r;
    const frac = total ? passed / total : 0;
    return (
        <svg width="60" height="60" viewBox="0 0 72 72" role="img" aria-label={`${passed} of ${total} checks pass`}>
            <circle cx="36" cy="36" r={r} fill="none" stroke="#ECEAE3" strokeWidth="9" />
            <circle cx="36" cy="36" r={r} fill="none" stroke="#1C1B22" strokeWidth="9" strokeLinecap="round"
                strokeDasharray={`${c * frac} ${c}`} transform="rotate(-90 36 36)" />
            <text x="36" y="42" textAnchor="middle" fontSize="17" fontWeight="700" fill="#1C1B22">{passed}/{total}</text>
        </svg>
    );
}

// --- page -------------------------------------------------------------------------------

export default function QualityDashboard() {
    const projectId = useActiveProjectId();
    const [days, setDays] = useState(7);
    const [showRules, setShowRules] = useState(false);
    const [selectedName, setSelectedName] = useState<string | null>(null);

    const dash = useQuery({
        queryKey: ['quality-dash', projectId, days],
        queryFn: () => qualityApi.dashboard(projectId!, days),
        enabled: !!projectId,
    });
    const gate = useQuery({
        queryKey: ['quality-gate', projectId],
        queryFn: () => qualityApi.gate(projectId!),
        enabled: !!projectId,
    });
    const externalQ = useQuery({
        queryKey: ['external-reports', projectId],
        queryFn: () => qualityApi.externalReports(projectId!),
        enabled: !!projectId,
    });

    const checks = gate.data?.checks ?? [];
    const blocking = checks.filter((c) => !c.passed);
    const passing = checks.filter((c) => c.passed);
    const selected = checks.find((c) => c.name === selectedName) ?? blocking[0] ?? passing[0] ?? null;
    const d = dash.data;
    const sec = d?.security_findings || {};
    const latestExternal = externalQ.data?.[0];

    const checkLinks = (c: QualityGateCheck) => {
        const base = describe(c).links;
        if (c.name === 'external_tests' && latestExternal) {
            const broken = latestExternal.failures + latestExternal.errors;
            return [{ tag: 'junit', text: `${latestExternal.suite_name || latestExternal.source}: ${latestExternal.tests} tests, ${broken ? `${broken} failed` : 'all green'}` }];
        }
        if (c.name === 'monitors_up' && d?.down_monitor_names.length) {
            return [...d.down_monitor_names.map((n) => ({ tag: 'down', text: n, to: '/monitors' })), ...base];
        }
        return base;
    };

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Releases"
                badge={gate.data && (
                    <Pill tone={gate.data.passed ? 'mint' : 'peach'}>
                        {gate.data.passed ? 'Ready to ship' : `Not ready · ${blocking.length} blocker${blocking.length === 1 ? '' : 's'}`}
                    </Pill>
                )}
                subtitle={
                    gate.data
                        ? <>Gate evaluated on {gate.data.evaluated_run_ids.length ? `${gate.data.evaluated_run_ids.length === 1 ? 'run' : 'runs'} ${gate.data.evaluated_run_ids.map((id) => `#${id}`).join(', ')}` : 'no runs yet'}{gate.data.git_branch ? ` · ${gate.data.git_branch}` : ''}{gate.data.git_commit ? ` · ${gate.data.git_commit.slice(0, 8)}` : ''}{gate.data.policy_version ? ` · rules v${gate.data.policy_version}` : ''}{gate.data.ignored_run_ids?.length ? ` · ${gate.data.ignored_run_ids.length} cancelled / timed-out run(s) not counted` : ''}</>
                        : 'The release gate turns run health, monitors and security into a go / no-go.'
                }
                actions={projectId ? (
                    <>
                        <Button variant="outline" onClick={() => setShowRules((s) => !s)} aria-expanded={showRules}>Gate rules</Button>
                        <Button onClick={() => gate.refetch()} disabled={gate.isFetching}>
                            <RefreshCw className={cn('h-4 w-4', gate.isFetching && 'animate-spin')} /> Re-evaluate
                        </Button>
                    </>
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see whether it is ready to ship.</EmptyState>
            ) : (
                <>
                    {showRules && <PolicyPanel projectId={projectId} />}

                    {gate.isLoading ? (
                        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                            {[0, 1, 2].map((i) => <div key={i} className="h-[56px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                        </div>
                    ) : gate.error ? (
                        <EmptyState title="Couldn't evaluate the gate">{errDetail(gate.error)}</EmptyState>
                    ) : (
                        <SplitView
                            list={
                                <>
                                    <div className="flex items-center gap-3.5 rounded-[20px] border border-border bg-white p-4">
                                        <Ring passed={passing.length} total={checks.length} />
                                        <span className="flex flex-col gap-0.5">
                                            <span className="text-[15px] font-semibold text-ink">
                                                {blocking.length === 0 ? 'Every check passes' : `${blocking.length} check${blocking.length === 1 ? '' : 's'} blocking the release`}
                                            </span>
                                            <span className="text-[13px] leading-snug text-ink-soft">
                                                {blocking.length === 0 ? 'Nothing stands between this project and a release.' : 'Fix these, or change the rules if they are wrong for this project.'}
                                            </span>
                                        </span>
                                    </div>
                                    {[{ name: 'Blocking', items: blocking }, { name: 'Passing', items: passing }].filter((g) => g.items.length).map((g) => (
                                        <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                            {g.items.map((c) => (
                                                <ListRow
                                                    key={c.name}
                                                    selected={selected?.name === c.name}
                                                    onSelect={() => setSelectedName(c.name)}
                                                    leading={<KindChip kind={c.passed ? 'pass' : 'block'} size="sm" />}
                                                    title={describe(c).title}
                                                    trailing={<span className="font-mono text-[13px]">{c.actual}</span>}
                                                />
                                            ))}
                                        </ListGroup>
                                    ))}
                                </>
                            }
                            detail={selected ? (
                                <DetailCard label="Selected check">
                                    <div className="flex flex-col gap-2">
                                        <Pill tone={selected.passed ? 'mint' : 'peach'} className="self-start">{selected.passed ? 'Passing' : 'Blocking'}</Pill>
                                        <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{describe(selected).title}</h2>
                                    </div>
                                    <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                                        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
                                            <Eyebrow>Now</Eyebrow>
                                            <span className="text-[28px] font-bold tracking-tight text-ink">{selected.actual}</span>
                                        </div>
                                        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
                                            <Eyebrow>Rule</Eyebrow>
                                            <span className="pt-1.5 text-lg font-semibold text-ink">{selected.threshold}</span>
                                        </div>
                                    </div>
                                    {(describe(selected).explain || selected.detail) && (
                                        <Panel title="What this check looks at" callout>
                                            {describe(selected).explain}{selected.detail ? <> <span className="text-ink-soft">({selected.detail})</span></> : null}
                                        </Panel>
                                    )}
                                    <EvidenceList items={checkLinks(selected)} />
                                </DetailCard>
                            ) : (
                                <EmptyState title="No checks configured">Open Gate rules to decide what a release must pass.</EmptyState>
                            )}
                        />
                    )}

                    <section aria-labelledby="health-h" className="flex flex-col gap-3 pt-2">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                            <h2 id="health-h" className="text-lg font-semibold text-ink">Project health</h2>
                            <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                                <SelectTrigger className="h-9 w-[120px] rounded-full bg-white" aria-label="Time window"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="7">7 days</SelectItem>
                                    <SelectItem value="14">14 days</SelectItem>
                                    <SelectItem value="30">30 days</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        {dash.isLoading ? (
                            <div className="h-24 animate-pulse rounded-2xl bg-white/70" />
                        ) : dash.error ? (
                            <p className="text-sm text-ink-soft">Couldn't load health: {errDetail(dash.error)}</p>
                        ) : d && (
                            <>
                                <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                                    <Tile label="Pass rate" value={`${d.pass_rate}%`} sub={`${d.passed_runs} of ${d.finished_runs} finished runs`} />
                                    <Tile label="Runs" value={d.total_runs} sub={`last ${d.window_days} days`} />
                                    <Tile label="Flaky tests" value={d.flaky_tests} sub={`${d.quarantined_tests} quarantined`} />
                                    <Tile label="Monitors down" value={d.monitors_down} sub={`of ${d.monitors_total}`} />
                                    <Tile label="Security findings" value={`${sec.high || 0} high`} sub={`${sec.medium || 0} medium · ${sec.low || 0} low`} />
                                </div>
                                <div className="grid gap-3 lg:grid-cols-2">
                                    <div className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6">
                                        <Eyebrow right={<Link to="/analytics" className="underline-offset-2 hover:underline">Analytics</Link>}>Daily pass rate</Eyebrow>
                                        <TrendBars trend={d.trend} />
                                    </div>
                                    <div className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6">
                                        <Eyebrow>External CI results (ingested JUnit)</Eyebrow>
                                        {(externalQ.data?.length ?? 0) === 0 ? (
                                            <p className="text-sm text-ink-soft">None yet. CI can push JUnit XML to <span className="font-mono text-[13px]">POST /api/projects/{projectId}/external-results</span> with an API key.</p>
                                        ) : (
                                            <ul className="flex flex-col gap-2">
                                                {externalQ.data!.map((r) => {
                                                    const broken = r.failures + r.errors;
                                                    return (
                                                        <li key={r.id} className="flex items-center justify-between gap-3 text-sm">
                                                            <span className="flex min-w-0 items-center gap-2">
                                                                <span className={cn('h-2 w-2 flex-none rounded-full', broken ? 'bg-[#E0603A]' : 'bg-[#8FD3B6]')} />
                                                                <span className="truncate font-medium">{r.suite_name || r.source}</span>
                                                                {r.git_commit && <span className="font-mono text-xs text-ink-soft">{r.git_commit.slice(0, 8)}</span>}
                                                            </span>
                                                            <span className="flex-none text-xs tabular-nums text-ink-soft">{r.tests} tests · {broken ? `${broken} failed` : 'all green'}</span>
                                                        </li>
                                                    );
                                                })}
                                            </ul>
                                        )}
                                    </div>
                                </div>
                            </>
                        )}
                    </section>
                </>
            )}
        </div>
    );
}
