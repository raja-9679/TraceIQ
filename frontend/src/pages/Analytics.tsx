import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    BarChart3, RefreshCw, AlertCircle, Layers, Gauge, TimerReset, Bug, Send, Plus, Trash2, Clock,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { analyticsApi } from '@/api/analytics';
import { reportsApi, ReportSchedule } from '@/api/reports';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const fmtMs = (ms: number | null) => ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;

function Stat({ label, value, icon: Icon, tone = 'slate' }: { label: string; value: React.ReactNode; icon: any; tone?: string }) {
    const t: Record<string, string> = { slate: 'text-slate-700', emerald: 'text-emerald-600', rose: 'text-rose-600', amber: 'text-amber-600' };
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</span>
                <Icon className={`w-4 h-4 ${t[tone]}`} />
            </div>
            <div className={`text-3xl font-extrabold tabular-nums ${t[tone]}`}>{value}</div>
        </div>
    );
}

function ReportsSection({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [adding, setAdding] = useState(false);
    const [f, setF] = useState({ name: '', cron_expression: '0 9 * * 1', window_days: 7, channels: 'slack', recipients: '' });
    const reports = useQuery({ queryKey: ['reports', projectId], queryFn: () => reportsApi.list(projectId) });

    const create = useMutation({
        mutationFn: () => reportsApi.create(projectId, {
            name: f.name, cron_expression: f.cron_expression, window_days: f.window_days,
            channels: f.channels.split(',').map((s) => s.trim()).filter(Boolean),
            recipients: f.recipients.split(',').map((s) => s.trim()).filter(Boolean),
        }),
        onSuccess: () => { toast.success('Report scheduled'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); setAdding(false); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const sendNow = useMutation({ mutationFn: (id: number) => reportsApi.sendNow(id), onSuccess: () => toast.success('Report queued'), onError: (e) => toast.error(errDetail(e)) });
    const del = useMutation({ mutationFn: (id: number) => reportsApi.remove(id), onSuccess: () => { toast.success('Removed'); qc.invalidateQueries({ queryKey: ['reports', projectId] }); }, onError: (e) => toast.error(errDetail(e)) });

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-4">
                <h3 className="font-bold text-slate-800 flex items-center gap-2"><Clock className="w-4 h-4 text-indigo-500" /> Scheduled reports</h3>
                {!adding && <Button size="sm" variant="outline" className="h-8 rounded-lg text-xs" onClick={() => setAdding(true)}><Plus className="w-3.5 h-3.5 mr-1" /> New</Button>}
            </div>
            {adding && (
                <div className="grid sm:grid-cols-2 gap-2 mb-4 p-3 bg-slate-50 rounded-xl">
                    <Input className="h-8" placeholder="Name (Weekly quality)" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
                    <Input className="h-8" placeholder="Cron (0 9 * * 1)" value={f.cron_expression} onChange={(e) => setF({ ...f, cron_expression: e.target.value })} />
                    <Input className="h-8" type="number" placeholder="Window days" value={f.window_days} onChange={(e) => setF({ ...f, window_days: parseInt(e.target.value) || 7 })} />
                    <Input className="h-8" placeholder="Channels (slack,email)" value={f.channels} onChange={(e) => setF({ ...f, channels: e.target.value })} />
                    <Input className="h-8 sm:col-span-2" placeholder="Email recipients (comma-separated)" value={f.recipients} onChange={(e) => setF({ ...f, recipients: e.target.value })} />
                    <div className="flex gap-2 sm:col-span-2">
                        <Button size="sm" className="h-8 rounded-lg" onClick={() => create.mutate()} disabled={create.isPending || !f.name || !f.cron_expression}>Create</Button>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg" onClick={() => setAdding(false)}>Cancel</Button>
                    </div>
                </div>
            )}
            {!reports.data || reports.data.length === 0 ? (
                <p className="text-sm text-slate-400">No scheduled reports. Create one to get a recurring quality summary in Slack/Teams/email.</p>
            ) : (
                <div className="space-y-2">
                    {reports.data.map((r: ReportSchedule) => (
                        <div key={r.id} className="flex items-center justify-between text-sm border border-slate-100 rounded-lg px-3 py-2">
                            <div>
                                <span className="font-semibold text-slate-800">{r.name}</span>
                                <span className="text-xs text-slate-400 ml-2">{r.cron_expression} · {r.window_days}d · {(r.channels || []).join(', ') || 'slack'}</span>
                            </div>
                            <div className="flex items-center gap-2">
                                <Button size="sm" variant="outline" className="h-7 rounded text-xs gap-1" onClick={() => sendNow.mutate(r.id)}><Send className="w-3 h-3" /> Send now</Button>
                                <Button size="sm" variant="outline" className="h-7 rounded text-xs text-rose-600 border-rose-200 hover:bg-rose-50" onClick={() => del.mutate(r.id)}><Trash2 className="w-3 h-3" /></Button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}

export default function Analytics() {
    const [projectId, setProjectId] = useState<number | null>(() => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; });
    const [days, setDays] = useState(30);
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => { const id = parseInt(idStr); setProjectId(id); localStorage.setItem('activeProjectId', id.toString()); window.dispatchEvent(new Event('projectChanged')); };

    const summary = useQuery({ queryKey: ['eff-summary', projectId, days], queryFn: () => analyticsApi.summary(projectId!, days), enabled: !!projectId });
    const eff = useQuery({ queryKey: ['eff', projectId, days], queryFn: () => analyticsApi.effectiveness(projectId!, days, 50), enabled: !!projectId });
    const s = summary.data;

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Insights</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Test Analytics</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Which tests earn their keep — what fails, what's slow, what's flaky — plus MTTR and
                            scheduled reports for the people who sign off on quality.
                        </p>
                    </div>
                    <div className="sm:ml-auto flex items-end gap-3 shrink-0">
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Window</p>
                            <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                                <SelectTrigger className="w-[110px] h-10 rounded-xl bg-white border-slate-200"><SelectValue /></SelectTrigger>
                                <SelectContent><SelectItem value="7">7 days</SelectItem><SelectItem value="30">30 days</SelectItem><SelectItem value="90">90 days</SelectItem></SelectContent>
                            </Select>
                        </div>
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                            <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                                <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200"><div className="flex items-center gap-2 min-w-0"><Layers className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Select a project" /></div></SelectTrigger>
                                <SelectContent>{(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">Select a project to see analytics.</p></div>
            ) : summary.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : summary.error ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl"><AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" /><p className="text-rose-700 font-semibold">{errDetail(summary.error)}</p></div>
            ) : s && (
                <div className="space-y-6">
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        <Stat label="Pass rate" value={`${s.pass_rate}%`} icon={Gauge} tone={s.pass_rate >= 90 ? 'emerald' : s.pass_rate >= 60 ? 'amber' : 'rose'} />
                        <Stat label="Finished runs" value={s.total_finished_runs} icon={BarChart3} />
                        <Stat label="Open clusters" value={s.open_clusters} icon={Bug} tone={s.open_clusters ? 'rose' : 'emerald'} />
                        <Stat label="MTTR" value={s.mttr_hours == null ? '—' : `${s.mttr_hours}h`} icon={TimerReset} tone="slate" />
                    </div>

                    <div className="grid lg:grid-cols-3 gap-6">
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <h3 className="font-bold text-slate-800 mb-3">Top failing</h3>
                            {s.top_failing_tests.length === 0 ? <p className="text-sm text-slate-400">None 🎉</p> : s.top_failing_tests.map((t) => (
                                <div key={t.test_name} className="flex justify-between text-sm py-1 border-b border-slate-50"><span className="text-slate-600 truncate mr-2">{t.test_name}</span><span className="text-rose-600 font-semibold tabular-nums">{t.failures}</span></div>
                            ))}
                        </div>
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <h3 className="font-bold text-slate-800 mb-3">Slowest</h3>
                            {s.slowest_tests.map((t) => (
                                <div key={t.test_name} className="flex justify-between text-sm py-1 border-b border-slate-50"><span className="text-slate-600 truncate mr-2">{t.test_name}</span><span className="text-slate-500 tabular-nums">{fmtMs(t.avg_duration_ms)}</span></div>
                            ))}
                        </div>
                        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                            <h3 className="font-bold text-slate-800 mb-3">Flakiest</h3>
                            {s.flakiest_tests.length === 0 ? <p className="text-sm text-slate-400">None</p> : s.flakiest_tests.map((t) => (
                                <div key={t.test_name} className="flex justify-between text-sm py-1 border-b border-slate-50"><span className="text-slate-600 truncate mr-2">{t.test_name}</span><span className="text-amber-600 font-semibold tabular-nums">{Math.round(t.flake_score * 100)}%</span></div>
                            ))}
                        </div>
                    </div>

                    <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
                        <div className="p-4 border-b border-slate-100"><h3 className="font-bold text-slate-800">Per-test effectiveness</h3></div>
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader className="bg-slate-50/50"><TableRow>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Test</TableHead>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Runs</TableHead>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Failures</TableHead>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Rate</TableHead>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Clusters</TableHead>
                                    <TableHead className="text-xs uppercase tracking-widest text-slate-500">Avg</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {(eff.data || []).map((e) => (
                                        <TableRow key={e.test_name}>
                                            <TableCell className="font-medium text-slate-700 max-w-[320px] truncate">{e.test_name}</TableCell>
                                            <TableCell className="tabular-nums text-slate-500">{e.runs}</TableCell>
                                            <TableCell className="tabular-nums">{e.failures > 0 ? <span className="text-rose-600 font-semibold">{e.failures}</span> : <span className="text-slate-400">0</span>}</TableCell>
                                            <TableCell className="tabular-nums text-slate-600">{e.failure_rate}%</TableCell>
                                            <TableCell className="tabular-nums">{e.clusters_surfaced > 0 ? <Badge variant="outline" className="rounded-md bg-violet-50 text-violet-700 border-violet-200">{e.clusters_surfaced}</Badge> : <span className="text-slate-300">—</span>}</TableCell>
                                            <TableCell className="tabular-nums text-slate-500">{fmtMs(e.avg_duration_ms)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </div>

                    <ReportsSection projectId={projectId} />
                </div>
            )}
        </div>
    );
}
