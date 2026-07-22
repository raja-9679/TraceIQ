import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    Radio, RefreshCw, AlertCircle, Layers, ArrowUpCircle, ArrowDownCircle, HelpCircle, Info,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getProjects } from '@/lib/api';
import { monitorsApi, MonitorStatus } from '@/api/monitors';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

function StateBadge({ state }: { state: string }) {
    if (state === 'up') return <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-emerald-50 text-emerald-700 border-emerald-200"><ArrowUpCircle className="w-3 h-3 mr-1" /> up</Badge>;
    if (state === 'down') return <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-rose-50 text-rose-700 border-rose-200"><ArrowDownCircle className="w-3 h-3 mr-1" /> down</Badge>;
    return <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-slate-50 text-slate-500 border-slate-200"><HelpCircle className="w-3 h-3 mr-1" /> unknown</Badge>;
}

function Uptime({ value }: { value: number | null }) {
    if (value === null || value === undefined) return <span className="text-slate-300 text-sm">—</span>;
    const tone = value >= 99 ? 'text-emerald-600' : value >= 95 ? 'text-amber-600' : 'text-rose-600';
    return <span className={`text-sm font-bold tabular-nums ${tone}`}>{value}%</span>;
}

function Sparkline({ checks }: { checks: MonitorStatus['recent_checks'] }) {
    // Most-recent-first from the API; show oldest→newest left→right.
    const items = [...checks].reverse().slice(-20);
    if (!items.length) return <span className="text-slate-300 text-xs">no checks</span>;
    return (
        <div className="flex items-center gap-0.5">
            {items.map((c) => (
                <span key={c.id} title={`${c.checked_at}: ${c.status}`}
                    className={`inline-block w-1.5 h-4 rounded-sm ${c.is_up ? 'bg-emerald-400' : 'bg-rose-400'}`} />
            ))}
        </div>
    );
}

export default function Monitors() {
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
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };

    const { data: monitors, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['monitors', projectId],
        queryFn: () => monitorsApi.list(projectId!),
        enabled: !!projectId,
        refetchInterval: 30000,
    });

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Monitoring</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Synthetic Monitors</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Scheduled suites run against production as health checks. Consecutive failures trigger
                            alerts; every check feeds uptime. Flag a schedule as a monitor to see it here.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
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

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Select a project above to see its monitors.</p>
                </div>
            ) : isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : error ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
                    <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
                    <p className="text-rose-700 font-semibold">Failed to load monitors</p>
                    <p className="text-rose-600/80 text-sm">{errDetail(error)}</p>
                </div>
            ) : !monitors || monitors.length === 0 ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <Radio className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No monitors yet</h3>
                    <p className="text-slate-500 text-sm max-w-md mx-auto">
                        Enable <span className="font-semibold">monitor mode</span> on a schedule (Schedules page) to turn its
                        recurring run into a production health check with uptime tracking and failure-streak alerts.
                    </p>
                </div>
            ) : (
                <>
                    <div className="flex items-center justify-between mb-3">
                        <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2 flex-1 mr-3">
                            <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
                            <p>Auto-refreshes every 30s. Uptime is computed from recorded checks over the window.</p>
                        </div>
                        <button onClick={() => refetch()} className="text-slate-400 hover:text-slate-700">
                            <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
                        </button>
                    </div>
                    <div className="grid md:grid-cols-2 gap-4">
                        {monitors.map((m) => (
                            <div key={m.schedule_id} className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                                <div className="flex items-start justify-between mb-3">
                                    <div>
                                        <div className="font-bold text-slate-800">{m.name}</div>
                                        <div className="text-xs text-slate-400 mt-0.5">
                                            schedule #{m.schedule_id}{!m.is_active && ' · paused'}
                                        </div>
                                    </div>
                                    <StateBadge state={m.state} />
                                </div>
                                <div className="grid grid-cols-3 gap-3 mb-3">
                                    <div>
                                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">24h uptime</div>
                                        <Uptime value={m.uptime_24h} />
                                    </div>
                                    <div>
                                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">7d uptime</div>
                                        <Uptime value={m.uptime_7d} />
                                    </div>
                                    <div>
                                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Fail streak</div>
                                        <span className={`text-sm font-bold tabular-nums ${m.consecutive_failures ? 'text-rose-600' : 'text-slate-400'}`}>
                                            {m.consecutive_failures}
                                        </span>
                                    </div>
                                </div>
                                <div className="flex items-center justify-between">
                                    <Sparkline checks={m.recent_checks} />
                                    <span className="text-[10px] text-slate-400">{m.total_checks} checks</span>
                                </div>
                            </div>
                        ))}
                    </div>
                </>
            )}
        </div>
    );
}
