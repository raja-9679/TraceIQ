import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    Bug, RefreshCw, AlertCircle, Layers, Ticket, ExternalLink, ChevronRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects, getWorkspaces } from '@/lib/api';
import { triageApi, FailureCluster } from '@/api/triage';
import { ticketsApi } from '@/api/tickets';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const CAT_TONE: Record<string, string> = {
    selector: 'bg-violet-50 text-violet-700 border-violet-200',
    timeout: 'bg-amber-50 text-amber-700 border-amber-200',
    assertion: 'bg-sky-50 text-sky-700 border-sky-200',
    network: 'bg-rose-50 text-rose-700 border-rose-200',
    navigation: 'bg-orange-50 text-orange-700 border-orange-200',
    other: 'bg-slate-50 text-slate-600 border-slate-200',
};
const STATUS_TONE: Record<string, string> = {
    open: 'bg-rose-50 text-rose-700 border-rose-200',
    investigating: 'bg-amber-50 text-amber-700 border-amber-200',
    resolved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    ignored: 'bg-slate-50 text-slate-500 border-slate-200',
};
const STATUSES = ['open', 'investigating', 'resolved', 'ignored'];

function ClusterCard({ cluster }: { cluster: FailureCluster }) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const detail = useQuery({ queryKey: ['cluster', cluster.id], queryFn: () => triageApi.get(cluster.id), enabled: open });

    // trackers for the one-ticket-per-cluster action
    const { data: workspaces } = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces, enabled: open });
    const wsId = workspaces?.[0]?.id ?? null;
    const trackers = useQuery({ queryKey: ['trackers', wsId], queryFn: () => ticketsApi.listConfigs(wsId!), enabled: open && !!wsId });
    const [trackerId, setTrackerId] = useState('');

    const setStatus = useMutation({
        mutationFn: (status: string) => triageApi.update(cluster.id, { status: status as FailureCluster['status'] }),
        onSuccess: () => { toast.success('Status updated'); qc.invalidateQueries({ queryKey: ['clusters'] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const fileTicket = useMutation({
        mutationFn: () => triageApi.createTicket(cluster.id, { config_id: parseInt(trackerId) }),
        onSuccess: () => toast.success('Ticket queued for this cluster'),
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
            <div className="flex items-center gap-3 p-4">
                <button onClick={() => setOpen(!open)} className="text-slate-400 hover:text-slate-700 shrink-0">
                    <ChevronRight className={`w-4 h-4 transition-transform ${open ? 'rotate-90' : ''}`} />
                </button>
                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => setOpen(!open)}>
                    <div className="font-semibold text-slate-800 text-sm truncate">{cluster.title}</div>
                    <div className="text-xs text-slate-400 mt-0.5">
                        last seen {new Date(cluster.last_seen_at).toLocaleString()}
                        {cluster.last_run_id ? ` · run #${cluster.last_run_id}` : ''}
                    </div>
                </div>
                <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase shrink-0 ${CAT_TONE[cluster.category] || CAT_TONE.other}`}>{cluster.category}</Badge>
                <span className="text-sm font-bold text-slate-700 tabular-nums shrink-0" title="occurrences">×{cluster.occurrence_count}</span>
                <Select value={cluster.status} onValueChange={(v) => setStatus.mutate(v)}>
                    <SelectTrigger className={`h-7 w-[130px] text-xs rounded-md border ${STATUS_TONE[cluster.status]}`}><SelectValue /></SelectTrigger>
                    <SelectContent>{STATUSES.map((st) => <SelectItem key={st} value={st}>{st}</SelectItem>)}</SelectContent>
                </Select>
            </div>
            {open && (
                <div className="border-t border-slate-100 p-4 bg-slate-50/30 space-y-4">
                    {cluster.sample_error && (
                        <pre className="text-xs bg-slate-900 text-slate-100 rounded-lg p-3 overflow-x-auto whitespace-pre-wrap">{cluster.sample_error}</pre>
                    )}
                    <div>
                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Occurrences</div>
                        {!detail.data ? <RefreshCw className="animate-spin w-4 h-4 text-slate-400" /> : (
                            <div className="space-y-1">
                                {detail.data.occurrences.map((o) => (
                                    <a key={o.result_id} href={`/runs/${o.run_id}`} className="flex items-center justify-between text-sm hover:bg-white rounded px-2 py-1">
                                        <span className="text-slate-600 truncate">{o.test_name}</span>
                                        <span className="text-xs text-indigo-600 shrink-0">run #{o.run_id} <ExternalLink className="w-3 h-3 inline" /></span>
                                    </a>
                                ))}
                            </div>
                        )}
                    </div>
                    <div className="flex items-end gap-2 border-t border-slate-100 pt-3">
                        <div className="flex-1 max-w-xs">
                            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">File one ticket for this cluster</div>
                            <Select value={trackerId} onValueChange={setTrackerId}>
                                <SelectTrigger className="h-8 text-xs"><SelectValue placeholder="Select tracker" /></SelectTrigger>
                                <SelectContent>{(trackers.data || []).filter((c) => c.enabled).map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name} ({c.provider})</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                        <Button size="sm" variant="outline" className="h-8 rounded-lg text-xs gap-1.5" disabled={!trackerId || fileTicket.isPending} onClick={() => fileTicket.mutate()}>
                            <Ticket className="w-3.5 h-3.5" /> Create ticket
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}

export default function Triage() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const [statusFilter, setStatusFilter] = useState('open');

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr); setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };
    const clusters = useQuery({
        queryKey: ['clusters', projectId, statusFilter],
        queryFn: () => triageApi.list(projectId!, statusFilter === 'all' ? undefined : statusFilter),
        enabled: !!projectId,
    });

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Triage</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Failure Triage</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Failures are fingerprinted and grouped by root cause, so one problem is one item —
                            not forty. Triage a cluster, then file a single ticket for the whole group.
                        </p>
                    </div>
                    <div className="sm:ml-auto flex items-end gap-3 shrink-0">
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Status</p>
                            <Select value={statusFilter} onValueChange={setStatusFilter}>
                                <SelectTrigger className="w-[140px] h-10 rounded-xl bg-white border-slate-200"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="open">Open</SelectItem>
                                    <SelectItem value="investigating">Investigating</SelectItem>
                                    <SelectItem value="resolved">Resolved</SelectItem>
                                    <SelectItem value="ignored">Ignored</SelectItem>
                                    <SelectItem value="all">All</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                            <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                                <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200">
                                    <div className="flex items-center gap-2 min-w-0"><Layers className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Select a project" /></div>
                                </SelectTrigger>
                                <SelectContent>{(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <p className="text-slate-500 text-sm">Select a project to see its failure clusters.</p>
                </div>
            ) : clusters.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : clusters.error ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
                    <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" /><p className="text-rose-700 font-semibold">{errDetail(clusters.error)}</p>
                </div>
            ) : !clusters.data || clusters.data.length === 0 ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <Bug className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No {statusFilter === 'all' ? '' : statusFilter} failure clusters</h3>
                    <p className="text-slate-500 text-sm max-w-md mx-auto">Failing runs are clustered automatically as they finalize.</p>
                </div>
            ) : (
                <div className="space-y-2">
                    {clusters.data.map((c) => <ClusterCard key={c.id} cluster={c} />)}
                </div>
            )}
        </div>
    );
}
