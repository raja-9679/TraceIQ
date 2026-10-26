import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
    AlertCircle, AlertTriangle, Activity, Cpu, RefreshCw, Server, ShieldAlert, Skull, Timer,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { fleetApi } from '@/api/fleet';
import { formatAge } from '@/lib/fleet';
import { DeadLetterPanel, JobQueuePanel } from '@/components/fleet/QueuePanels';

type ApiErr = { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
const errDetail = (err: unknown): string => {
    const e = err as ApiErr | null;
    return String(e?.response?.data?.detail || e?.message || 'Unknown error');
};

function Stat({ label, value, sub, icon: Icon, tone = 'text-slate-800' }: {
    label: string; value: React.ReactNode; sub?: string; icon: React.ComponentType<{ className?: string }>; tone?: string;
}) {
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</span>
                <Icon className="w-4 h-4 text-indigo-500" />
            </div>
            <div className={`text-3xl font-extrabold tabular-nums ${tone}`}>{value}</div>
            {sub && <div className="text-xs text-slate-400 mt-1">{sub}</div>}
        </div>
    );
}

const TH = 'font-bold text-slate-500 uppercase tracking-widest text-xs h-11';

export default function WorkerFleet() {
    const { data, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['worker-fleet'],
        queryFn: fleetApi.get,
        refetchInterval: 15000,
        retry: false,
    });
    const status = (error as ApiErr | null)?.response?.status;

    return (
        <div className="max-w-[1300px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Instance admin</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Worker Fleet</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Every execution worker heartbeats every {data?.heartbeat_interval_seconds ?? 15}s. A worker
                            that misses its heartbeat for {data?.heartbeat_ttl_seconds ?? 45}s is shown as lost. Job
                            backlog comes straight from the Redis streams.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => refetch()}
                        aria-label="Refresh worker fleet"
                        className="sm:ml-auto text-slate-400 hover:text-slate-700 flex items-center gap-1.5 text-sm"
                    >
                        <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} /> Refresh
                    </button>
                </div>
            </div>

            {isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : status === 403 ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <ShieldAlert className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">Instance admins only</h3>
                    <p className="text-slate-500 text-sm max-w-md mx-auto">
                        The worker fleet lists every worker host and region in this deployment, so it is only
                        visible to the instance administrator.
                    </p>
                </div>
            ) : error || !data ? (
                <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
                    <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
                    <p className="text-rose-700 font-semibold">Failed to load the worker fleet</p>
                    <p className="text-rose-600/80 text-sm">{errDetail(error)}</p>
                </div>
            ) : (
                <>
                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
                        <Stat label="Alive" value={data.summary.alive} icon={Server} tone="text-emerald-600" />
                        <Stat label="Lost" value={data.summary.lost} sub="missed heartbeat recently" icon={Skull}
                            tone={data.summary.lost ? 'text-rose-600' : 'text-slate-800'} />
                        <Stat label="Busy" value={data.summary.busy} sub="running a job now" icon={Activity} />
                        <Stat label="Version skew" value={data.summary.skewed} sub="older than step catalog" icon={Cpu}
                            tone={data.summary.skewed ? 'text-amber-600' : 'text-slate-800'} />
                        <Stat label="Dead letters" value={data.dead_letter_depth} icon={Timer}
                            tone={data.dead_letter_depth ? 'text-rose-600' : 'text-slate-800'} />
                    </div>

                    {data.warnings.length > 0 && (
                        <div role="alert" className="bg-amber-50 border border-amber-200 rounded-2xl px-5 py-4 mb-6">
                            <div className="flex items-start gap-3">
                                <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
                                <ul className="text-sm text-amber-800 space-y-1">
                                    {data.warnings.map((w) => <li key={w}>{w}</li>)}
                                </ul>
                            </div>
                        </div>
                    )}

                    <h2 className="text-lg font-bold text-slate-800 mb-3">Workers</h2>
                    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm mb-8">
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader className="bg-slate-50/50">
                                    <TableRow className="border-slate-100 hover:bg-transparent">
                                        <TableHead className={TH}>Worker</TableHead>
                                        <TableHead className={TH}>Kind / region</TableHead>
                                        <TableHead className={TH}>Version</TableHead>
                                        <TableHead className={TH}>Current job</TableHead>
                                        <TableHead className={TH}>Processed / failed</TableHead>
                                        <TableHead className={TH}>Started</TableHead>
                                        <TableHead className={TH}>Last seen</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {data.workers.length === 0 && (
                                        <TableRow>
                                            <TableCell colSpan={7} className="text-center text-sm text-slate-400 py-8">
                                                No worker has reported a heartbeat. Workers built before this release do not heartbeat — rebuild the execution-worker image.
                                            </TableCell>
                                        </TableRow>
                                    )}
                                    {data.workers.map((w) => (
                                        <TableRow key={w.worker_id} className="border-slate-100">
                                            <TableCell className="py-3">
                                                <div className="flex items-center gap-2">
                                                    <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${w.alive
                                                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                                        : 'bg-rose-50 text-rose-700 border-rose-200'}`}>
                                                        {w.alive ? 'alive' : 'lost'}
                                                    </Badge>
                                                    <code className="text-xs font-mono text-slate-700">{w.worker_id}</code>
                                                </div>
                                                <div className="text-xs text-slate-400 mt-0.5">{w.hostname || '—'}</div>
                                            </TableCell>
                                            <TableCell className="py-3 text-xs">
                                                <div className="font-semibold text-slate-700">{w.kind}</div>
                                                <div className="text-slate-400">{w.region || 'global pool'}</div>
                                                {w.workspace_id !== null && w.workspace_id !== undefined && (
                                                    <div className="text-slate-400">workspace #{w.workspace_id}</div>
                                                )}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs">
                                                <div className="text-slate-700">{w.version || '—'}</div>
                                                {w.git_sha && <code className="text-[10px] text-slate-400">{w.git_sha.slice(0, 10)}</code>}
                                                {w.version_skew && (
                                                    <Badge variant="outline" title={`Missing: ${w.missing_step_types.join(', ')}`}
                                                        className="mt-1 rounded-md text-[10px] font-bold bg-amber-50 text-amber-700 border-amber-200 block w-fit">
                                                        skew: {w.missing_step_types.length} missing
                                                    </Badge>
                                                )}
                                                {w.version_skew && (
                                                    <div className="text-[10px] text-amber-700 mt-1 max-w-[220px] break-words">
                                                        {w.missing_step_types.join(', ')}
                                                    </div>
                                                )}
                                                {!w.step_types_reported && w.alive && (
                                                    <div className="text-[10px] text-slate-400 mt-1">step types not reported</div>
                                                )}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs">
                                                {w.current_job ? (
                                                    <Link to={`/runs/${w.current_job.run_id}`} className="text-indigo-600 hover:underline">
                                                        run #{w.current_job.run_id}
                                                    </Link>
                                                ) : <span className="text-slate-400">idle</span>}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs tabular-nums">
                                                {w.jobs_processed} / <span className={w.jobs_failed ? 'text-rose-600' : ''}>{w.jobs_failed}</span>
                                            </TableCell>
                                            <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                                                {w.started_at ? new Date(w.started_at).toLocaleString() : '—'}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                                                {formatAge(w.age_seconds)} ago
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </div>

                    <JobQueuePanel />
                    <DeadLetterPanel />

                    <h2 className="text-lg font-bold text-slate-800 mb-3">Job streams</h2>
                    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader className="bg-slate-50/50">
                                    <TableRow className="border-slate-100 hover:bg-transparent">
                                        <TableHead className={TH}>Stream</TableHead>
                                        <TableHead className={TH}>Region</TableHead>
                                        <TableHead className={TH}>Length</TableHead>
                                        <TableHead className={TH}>Queued (lag)</TableHead>
                                        <TableHead className={TH}>In flight</TableHead>
                                        <TableHead className={TH}>In flight per consumer</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {data.streams.map((s) => (
                                        <TableRow key={s.stream} className="border-slate-100">
                                            <TableCell className="py-3"><code className="text-xs font-mono">{s.stream}</code>
                                                <div className="text-[10px] text-slate-400">{s.group}</div></TableCell>
                                            <TableCell className="py-3 text-xs">{s.region || 'global'}</TableCell>
                                            <TableCell className="py-3 text-xs tabular-nums">{s.length}</TableCell>
                                            <TableCell className={`py-3 text-xs tabular-nums ${s.lag ? 'font-bold text-amber-700' : ''}`}>
                                                {s.lag ?? '—'}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs tabular-nums">{s.pending}</TableCell>
                                            <TableCell className="py-3 text-xs">
                                                {s.consumers.filter((c) => c.pending > 0).length === 0 ? (
                                                    <span className="text-slate-400">—</span>
                                                ) : (
                                                    <div className="flex flex-wrap gap-1">
                                                        {s.consumers.filter((c) => c.pending > 0).map((c) => (
                                                            <code key={c.name} className="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded">
                                                                {c.name}: {c.pending}
                                                            </code>
                                                        ))}
                                                    </div>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    </div>
                    <p className="text-xs text-slate-400 mt-3">
                        Length counts every entry still stored in the stream (including acknowledged ones); "queued" is what no worker has picked up yet.
                    </p>
                </>
            )}
        </div>
    );
}
