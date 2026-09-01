import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    Gauge, Activity, ShieldAlert, RefreshCw, AlertCircle, Layers, CheckCircle2,
    XCircle, Save, TimerReset, TrendingUp,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { qualityApi, QualityGatePolicy, CiSettings } from '@/api/quality';
import { DataPolicySection } from '@/components/settings/DataPolicySection';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

function useActiveProject() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId');
        return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => {
            const s = localStorage.getItem('activeProjectId');
            setProjectId(s ? parseInt(s) : null);
        };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    return [projectId, setProjectId] as const;
}

function StatCard({ label, value, sub, tone = 'slate', icon: Icon }: {
    label: string; value: React.ReactNode; sub?: string; tone?: string; icon: any;
}) {
    const tones: Record<string, string> = {
        slate: 'text-slate-700', emerald: 'text-emerald-600', rose: 'text-rose-600',
        amber: 'text-amber-600', indigo: 'text-indigo-600',
    };
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</span>
                <Icon className={`w-4 h-4 ${tones[tone]}`} />
            </div>
            <div className={`text-3xl font-extrabold tabular-nums ${tones[tone]}`}>{value}</div>
            {sub && <div className="text-xs text-slate-400 mt-1">{sub}</div>}
        </div>
    );
}

function TrendBars({ trend }: { trend: { date: string; pass_rate: number; runs: number }[] }) {
    if (!trend.length) return <p className="text-sm text-slate-400">No finished runs in this window.</p>;
    return (
        <div className="flex items-end gap-2 h-28">
            {trend.map((p) => {
                const tone = p.pass_rate >= 90 ? 'bg-emerald-500' : p.pass_rate >= 60 ? 'bg-amber-500' : 'bg-rose-500';
                return (
                    <div key={p.date} className="flex-1 flex flex-col items-center gap-1 group">
                        <div className="w-full flex items-end h-20">
                            <div className={`w-full rounded-t ${tone} transition-all`} style={{ height: `${Math.max(4, p.pass_rate)}%` }}
                                title={`${p.date}: ${p.pass_rate}% (${p.runs} runs)`} />
                        </div>
                        <span className="text-[9px] text-slate-400 tabular-nums">{p.date.slice(5)}</span>
                    </div>
                );
            })}
        </div>
    );
}

const SEV_TONE: Record<string, string> = {
    high: 'bg-rose-50 text-rose-700 border-rose-200',
    medium: 'bg-amber-50 text-amber-700 border-amber-200',
    low: 'bg-sky-50 text-sky-700 border-sky-200',
    info: 'bg-slate-50 text-slate-600 border-slate-200',
};

export default function QualityDashboard() {
    const [projectId, setProjectId] = useActiveProject();
    const [days, setDays] = useState(7);
    const qc = useQueryClient();

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr);
        setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

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
    const policyQ = useQuery({
        queryKey: ['quality-policy', projectId],
        queryFn: () => qualityApi.getPolicy(projectId!),
        enabled: !!projectId,
    });
    const ciQ = useQuery({
        queryKey: ['ci-settings', projectId],
        queryFn: () => qualityApi.getCiSettings(projectId!),
        enabled: !!projectId,
    });
    const externalQ = useQuery({
        queryKey: ['external-reports', projectId],
        queryFn: () => qualityApi.externalReports(projectId!),
        enabled: !!projectId,
    });

    const [policy, setPolicy] = useState<QualityGatePolicy | null>(null);
    const [ci, setCi] = useState<CiSettings | null>(null);
    useEffect(() => { if (policyQ.data) setPolicy(policyQ.data); }, [policyQ.data]);
    useEffect(() => { if (ciQ.data) setCi(ciQ.data); }, [ciQ.data]);

    const savePolicy = useMutation({
        mutationFn: () => qualityApi.setPolicy(projectId!, policy!),
        onSuccess: () => { toast.success('Gate policy saved'); qc.invalidateQueries({ queryKey: ['quality-gate', projectId] }); },
        onError: (e) => toast.error(`Save failed: ${errDetail(e)}`),
    });
    const saveCi = useMutation({
        mutationFn: () => qualityApi.setCiSettings(projectId!, ci!),
        onSuccess: () => toast.success('CI settings saved'),
        onError: (e) => toast.error(`Save failed: ${errDetail(e)}`),
    });

    const d = dash.data;
    const sec = d?.security_findings || {};

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Quality</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Quality Dashboard</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Run health, flakiness, monitor uptime and security posture in one place — plus the release
                            gate that turns it all into a go/no-go.
                        </p>
                    </div>
                    <div className="sm:ml-auto flex items-end gap-3 shrink-0">
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Window</p>
                            <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                                <SelectTrigger className="w-[110px] h-10 rounded-xl bg-white border-slate-200"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="7">7 days</SelectItem>
                                    <SelectItem value="14">14 days</SelectItem>
                                    <SelectItem value="30">30 days</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                            <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                                <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200">
                                    <div className="flex items-center gap-2 min-w-0">
                                        <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                                        <SelectValue placeholder="Select a project" />
                                    </div>
                                </SelectTrigger>
                                <SelectContent>
                                    {(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Select a project above to see its quality snapshot.</p>
                </div>
            ) : dash.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : dash.error ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
                    <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
                    <p className="text-rose-700 font-semibold">Failed to load dashboard</p>
                    <p className="text-rose-600/80 text-sm">{errDetail(dash.error)}</p>
                </div>
            ) : d && (
                <div className="space-y-6">
                    {/* KPI cards */}
                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
                        <StatCard label="Pass rate" value={`${d.pass_rate}%`} sub={`${d.passed_runs}/${d.finished_runs} finished runs`}
                            tone={d.pass_rate >= 90 ? 'emerald' : d.pass_rate >= 60 ? 'amber' : 'rose'} icon={Gauge} />
                        <StatCard label="Total runs" value={d.total_runs} sub={`${d.window_days}d window`} tone="slate" icon={TrendingUp} />
                        <StatCard label="Flaky" value={d.flaky_tests} sub={`${d.quarantined_tests} quarantined`}
                            tone={d.flaky_tests ? 'amber' : 'slate'} icon={Activity} />
                        <StatCard label="Monitors down" value={d.monitors_down} sub={`${d.monitors_total} monitors`}
                            tone={d.monitors_down ? 'rose' : 'emerald'} icon={TimerReset} />
                        <StatCard label="Security" value={`${sec.high || 0}H / ${sec.medium || 0}M`} sub="findings in window"
                            tone={(sec.high || 0) ? 'rose' : (sec.medium || 0) ? 'amber' : 'slate'} icon={ShieldAlert} />
                    </div>

                    {/* Trend + gate */}
                    <div className="grid lg:grid-cols-2 gap-6">
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <h3 className="font-bold text-slate-800 mb-4">Pass-rate trend</h3>
                            <TrendBars trend={d.trend} />
                        </div>

                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="font-bold text-slate-800">Release gate</h3>
                                <Button size="sm" variant="outline" className="h-8 rounded-lg text-xs"
                                    onClick={() => gate.refetch()} disabled={gate.isFetching}>
                                    <RefreshCw className={`w-3.5 h-3.5 mr-1 ${gate.isFetching ? 'animate-spin' : ''}`} /> Re-evaluate
                                </Button>
                            </div>
                            {gate.isLoading ? <RefreshCw className="animate-spin w-5 h-5 text-slate-400" /> : gate.data && (
                                <>
                                    <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-bold mb-3 ${gate.data.passed ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
                                        {gate.data.passed ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                                        {gate.data.passed ? 'PASS' : 'FAIL'}
                                        <span className="font-normal text-xs opacity-70">
                                            runs {gate.data.evaluated_run_ids.join(', ') || 'none'}
                                        </span>
                                    </div>
                                    <div className="space-y-1.5">
                                        {gate.data.checks.map((c) => (
                                            <div key={c.name} className="flex items-center justify-between text-sm border-b border-slate-50 pb-1.5">
                                                <span className="flex items-center gap-2">
                                                    {c.passed ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" /> : <XCircle className="w-3.5 h-3.5 text-rose-500" />}
                                                    <span className="text-slate-600">{c.name}</span>
                                                </span>
                                                <span className="text-slate-400 text-xs tabular-nums">{c.actual} <span className="text-slate-300">/</span> {c.threshold}</span>
                                            </div>
                                        ))}
                                    </div>
                                </>
                            )}
                        </div>
                    </div>

                    {/* Policy + CI settings */}
                    <div className="grid lg:grid-cols-2 gap-6">
                        {policy && (
                            <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                                <h3 className="font-bold text-slate-800 mb-4">Gate policy</h3>
                                <div className="space-y-3">
                                    <label className="flex items-center justify-between text-sm">
                                        <span className="text-slate-600">Min pass rate (%)</span>
                                        <Input type="number" className="w-24 h-8" value={policy.min_pass_rate}
                                            onChange={(e) => setPolicy({ ...policy, min_pass_rate: parseFloat(e.target.value) || 0 })} />
                                    </label>
                                    <label className="flex items-center justify-between text-sm">
                                        <span className="text-slate-600">Max high findings</span>
                                        <Input type="number" className="w-24 h-8" value={policy.max_high_severity_findings}
                                            onChange={(e) => setPolicy({ ...policy, max_high_severity_findings: parseInt(e.target.value) || 0 })} />
                                    </label>
                                    <label className="flex items-center justify-between text-sm">
                                        <span className="text-slate-600">Max medium findings (blank = no limit)</span>
                                        <Input type="number" className="w-24 h-8" value={policy.max_medium_severity_findings ?? ''}
                                            onChange={(e) => setPolicy({ ...policy, max_medium_severity_findings: e.target.value === '' ? null : parseInt(e.target.value) })} />
                                    </label>
                                    <label className="flex items-center justify-between text-sm cursor-pointer">
                                        <span className="text-slate-600">Require monitors up</span>
                                        <input type="checkbox" className="w-4 h-4" checked={policy.require_monitors_up}
                                            onChange={(e) => setPolicy({ ...policy, require_monitors_up: e.target.checked })} />
                                    </label>
                                    <label className="flex items-center justify-between text-sm cursor-pointer">
                                        <span className="text-slate-600">Require external CI tests green (ingested JUnit)</span>
                                        <input type="checkbox" className="w-4 h-4" checked={policy.require_external_tests_pass ?? false}
                                            onChange={(e) => setPolicy({ ...policy, require_external_tests_pass: e.target.checked })} />
                                    </label>
                                    <div className="pt-2 border-t border-slate-100">
                                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">Performance budgets (0 = off)</p>
                                        <div className="space-y-3">
                                            <label className="flex items-center justify-between text-sm">
                                                <span className="text-slate-600">Max LCP (ms)</span>
                                                <Input type="number" className="w-24 h-8" value={policy.max_lcp_ms ?? 0}
                                                    onChange={(e) => setPolicy({ ...policy, max_lcp_ms: parseInt(e.target.value) || 0 })} />
                                            </label>
                                            <label className="flex items-center justify-between text-sm">
                                                <span className="text-slate-600">Max CLS</span>
                                                <Input type="number" step="0.01" className="w-24 h-8" value={policy.max_cls ?? 0}
                                                    onChange={(e) => setPolicy({ ...policy, max_cls: parseFloat(e.target.value) || 0 })} />
                                            </label>
                                            <label className="flex items-center justify-between text-sm">
                                                <span className="text-slate-600">Max TTFB (ms)</span>
                                                <Input type="number" className="w-24 h-8" value={policy.max_ttfb_ms ?? 0}
                                                    onChange={(e) => setPolicy({ ...policy, max_ttfb_ms: parseInt(e.target.value) || 0 })} />
                                            </label>
                                        </div>
                                    </div>
                                </div>
                                <Button size="sm" className="mt-4 h-9 rounded-lg" onClick={() => savePolicy.mutate()} disabled={savePolicy.isPending}>
                                    <Save className="w-3.5 h-3.5 mr-1.5" /> Save policy
                                </Button>
                            </div>
                        )}

                        {ci && (
                            <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                                <h3 className="font-bold text-slate-800 mb-1">CI reporting</h3>
                                <p className="text-xs text-slate-400 mb-4">Opt-in and git-optional — controls how CI consumes the gate.</p>
                                <div className="space-y-3">
                                    {([
                                        ['enabled', 'Enabled'],
                                        ['enforce_gate', 'Enforce gate (block on fail)'],
                                        ['post_pr_comment', 'Post PR comment (VCS consumers)'],
                                    ] as const).map(([key, lbl]) => (
                                        <label key={key} className="flex items-center justify-between text-sm cursor-pointer">
                                            <span className="text-slate-600">{lbl}</span>
                                            <input type="checkbox" className="w-4 h-4" checked={(ci as any)[key]}
                                                onChange={(e) => setCi({ ...ci, [key]: e.target.checked })} />
                                        </label>
                                    ))}
                                </div>
                                <Button size="sm" className="mt-4 h-9 rounded-lg" onClick={() => saveCi.mutate()} disabled={saveCi.isPending}>
                                    <Save className="w-3.5 h-3.5 mr-1.5" /> Save CI settings
                                </Button>
                            </div>
                        )}
                    </div>

                    {/* Data-capture policy. Sits with the other per-project policies
                        rather than in global Settings, because it is scoped to a project
                        and reads alongside the gate it feeds. */}
                    {projectId && <DataPolicySection projectId={projectId} />}

                    {(externalQ.data?.length ?? 0) > 0 && (
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <h3 className="font-bold text-slate-800 mb-3">External CI results (ingested JUnit)</h3>
                            <div className="space-y-2">
                                {externalQ.data!.map((r) => {
                                    const broken = r.failures + r.errors;
                                    return (
                                        <div key={r.id} className="flex items-center justify-between text-sm border border-slate-100 rounded-lg px-3 py-2">
                                            <div className="min-w-0">
                                                <span className={`inline-block w-2 h-2 rounded-full mr-2 ${broken ? 'bg-rose-500' : 'bg-emerald-500'}`} />
                                                <span className="font-semibold text-slate-700">{r.suite_name || r.source}</span>
                                                {r.git_commit && <span className="text-xs text-slate-400 ml-2 font-mono">{r.git_commit.slice(0, 8)}</span>}
                                            </div>
                                            <span className="tabular-nums text-slate-500 text-xs shrink-0">
                                                {r.tests} tests · {broken ? `${broken} failed` : 'all green'} · {new Date(r.created_at).toLocaleString()}
                                            </span>
                                        </div>
                                    );
                                })}
                            </div>
                            <p className="text-[10px] text-slate-400 mt-2 italic">Push reports: POST /api/projects/{projectId}/external-results with JUnit XML body (X-API-Key auth).</p>
                        </div>
                    )}

                    {d.monitors_down > 0 && (
                        <div className="flex items-start gap-2 text-sm text-rose-600 bg-rose-50/60 border border-rose-100 rounded-xl px-3 py-2.5">
                            <ShieldAlert className="w-4 h-4 shrink-0 mt-px" />
                            <p><span className="font-semibold">Monitors down:</span> {d.down_monitor_names.join(', ')}</p>
                        </div>
                    )}

                    {Object.keys(sec).length > 0 && (
                        <div className="flex flex-wrap gap-2">
                            {(['high', 'medium', 'low', 'info'] as const).map((s) => sec[s] ? (
                                <Badge key={s} variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${SEV_TONE[s]}`}>{sec[s]} {s}</Badge>
                            ) : null)}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
