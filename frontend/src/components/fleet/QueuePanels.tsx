import { Fragment, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ChevronDown, ChevronRight, RotateCcw, Trash2, XCircle } from 'lucide-react';
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
    AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { fleetApi, type ClearQueueRequest, type ClearQueueResult, type QueuedJob } from '@/api/fleet';
import { formatAge, groupQueueByRun } from '@/lib/fleet';

const TH = 'font-bold text-slate-500 uppercase tracking-widest text-xs h-11';

type ApiErr = { response?: { data?: { detail?: unknown } }; message?: string };
const errDetail = (err: unknown): string => {
    const e = err as ApiErr | null;
    const d = e?.response?.data?.detail;
    return typeof d === 'string' ? d : String(e?.message || 'Unknown error');
};

function Confirm({ trigger, title, body, action, onConfirm }: {
    trigger: React.ReactNode; title: string; body: React.ReactNode; action: string; onConfirm: () => void;
}) {
    return (
        <AlertDialog>
            <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>{title}</AlertDialogTitle>
                    <AlertDialogDescription asChild><div>{body}</div></AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel>Keep</AlertDialogCancel>
                    <AlertDialogAction onClick={onConfirm} className="bg-rose-600 hover:bg-rose-700">{action}</AlertDialogAction>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );
}

function clearedMessage(r: ClearQueueResult): string {
    const parts = [`${r.cancelled_run_ids.length} run(s) cancelled`, `${r.jobs_deleted} job(s) removed`];
    if (r.jobs_left_running) parts.push(`${r.jobs_left_running} already on a worker will stop at their next step`);
    return parts.join(', ');
}

function jobLabel(j: QueuedJob): string {
    if (j.job_type !== 'test') return j.job_type.replace(/_/g, ' ');
    if (j.test_case_name) return j.test_case_name;
    if (j.case_count > 1) return `${j.case_count} cases`;
    return j.test_case_id ? `case #${j.test_case_id}` : 'job';
}

export function JobQueuePanel() {
    const qc = useQueryClient();
    const [open, setOpen] = useState<Record<string, boolean>>({});
    const { data, error, isLoading } = useQuery({
        queryKey: ['job-queue'], queryFn: fleetApi.queue, refetchInterval: 15000, retry: false,
    });
    const clear = useMutation({
        mutationFn: (body: ClearQueueRequest) => fleetApi.clearQueue(body),
        onSuccess: (r) => {
            toast.success(clearedMessage(r));
            qc.invalidateQueries({ queryKey: ['job-queue'] });
            qc.invalidateQueries({ queryKey: ['worker-fleet'] });
        },
        onError: (e) => toast.error(`Could not clear: ${errDetail(e)}`),
    });

    const jobs = (data?.streams ?? []).flatMap((s) => s.jobs);
    const total = (data?.streams ?? []).reduce((n, s) => n + s.total, 0);
    const truncated = (data?.streams ?? []).some((s) => s.total > s.shown);
    const groups = groupQueueByRun(jobs);
    const runCount = groups.filter((g) => g.runId !== null).length;

    return (
        <section className="mb-8">
            <div className="flex flex-wrap items-end gap-3 mb-3">
                <div>
                    <h2 className="text-lg font-bold text-slate-800">Waiting jobs</h2>
                    <p className="text-xs text-slate-500">
                        Jobs no worker has finished yet. Clearing a job cancels its whole run.
                    </p>
                </div>
                {total > 0 && (
                    <Confirm
                        trigger={
                            <Button variant="outline" size="sm" disabled={clear.isPending}
                                className="ml-auto text-rose-700 border-rose-200 hover:bg-rose-50">
                                <Trash2 className="w-4 h-4 mr-1.5" /> Clear whole queue
                            </Button>
                        }
                        title="Clear the whole queue?"
                        body={<>
                            <p>This cancels every run that has a job waiting{runCount ? ` (${runCount} shown here)` : ''} and
                                removes {total} waiting job(s). Cancelled runs keep the results they already have.</p>
                            <p className="mt-2">Jobs already on a worker stop at their next step.</p>
                        </>}
                        action="Cancel runs and clear"
                        onConfirm={() => clear.mutate({ clear_all: true })}
                    />
                )}
            </div>

            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                {isLoading ? (
                    <div className="p-8 text-center text-sm text-slate-400">Loading…</div>
                ) : error ? (
                    <div className="p-8 text-center text-sm text-rose-600">Failed to load the queue: {errDetail(error)}</div>
                ) : groups.length === 0 ? (
                    <div className="p-8 text-center text-sm text-slate-400">Nothing is waiting. Every queued job has been picked up and finished.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader className="bg-slate-50/50">
                                <TableRow className="border-slate-100 hover:bg-transparent">
                                    <TableHead className={TH}>Run</TableHead>
                                    <TableHead className={TH}>Project / suite</TableHead>
                                    <TableHead className={TH}>Jobs</TableHead>
                                    <TableHead className={TH}>Oldest waiting</TableHead>
                                    <TableHead className={`${TH} text-right`}>Action</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {groups.map((g) => {
                                    const key = String(g.runId ?? 'side');
                                    const run = g.runId !== null ? data?.runs[String(g.runId)] : undefined;
                                    const expanded = !!open[key];
                                    return (
                                        <Fragment key={key}>
                                            <TableRow className="border-slate-100">
                                                <TableCell className="py-3 text-xs">
                                                    <button type="button" aria-label={expanded ? 'Hide jobs' : 'Show jobs'}
                                                        onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}
                                                        className="inline-flex items-center gap-1 text-slate-500 hover:text-slate-800">
                                                        {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                                                    </button>{' '}
                                                    {g.runId !== null ? (
                                                        <Link to={`/runs/${g.runId}`} className="text-indigo-600 hover:underline font-semibold">run #{g.runId}</Link>
                                                    ) : <span className="font-semibold text-slate-700">Background checks</span>}
                                                    {run && (
                                                        <Badge variant="outline" className="ml-2 rounded-md text-[10px] font-bold uppercase">
                                                            {run.status.toLowerCase()}
                                                        </Badge>
                                                    )}
                                                </TableCell>
                                                <TableCell className="py-3 text-xs">
                                                    {g.runId === null ? (
                                                        <span className="text-slate-400">crawls, page checks, persona refreshes</span>
                                                    ) : run ? (
                                                        <>
                                                            <div className="font-semibold text-slate-700">{run.project_name || '—'}</div>
                                                            <div className="text-slate-400">{run.suite_name || run.test_case_name || ''}</div>
                                                        </>
                                                    ) : <span className="text-slate-400">run not found</span>}
                                                </TableCell>
                                                <TableCell className="py-3 text-xs tabular-nums">
                                                    {g.jobs.length} waiting
                                                    {g.claimed > 0 && <div className="text-slate-400">{g.claimed} on a worker</div>}
                                                </TableCell>
                                                <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                                                    {formatAge(g.oldestWaitingSeconds)}
                                                </TableCell>
                                                <TableCell className="py-3 text-right">
                                                    <Confirm
                                                        trigger={
                                                            <Button variant="ghost" size="sm" disabled={clear.isPending}
                                                                className="text-rose-700 hover:bg-rose-50">
                                                                <XCircle className="w-4 h-4 mr-1.5" />
                                                                {g.runId !== null ? 'Cancel run' : 'Clear'}
                                                            </Button>
                                                        }
                                                        title={g.runId !== null ? `Cancel run #${g.runId}?` : 'Clear background checks?'}
                                                        body={g.runId !== null
                                                            ? <p>The run is marked cancelled and its {g.jobs.length} waiting job(s) are removed.
                                                                {g.claimed > 0 && ` ${g.claimed} already on a worker stop at their next step.`}</p>
                                                            : <p>Removes the waiting background jobs. Their checks record an error. Jobs already on a worker finish.</p>}
                                                        action={g.runId !== null ? 'Cancel run' : 'Clear'}
                                                        onConfirm={() => clear.mutate(g.runId !== null
                                                            ? { run_ids: [g.runId] }
                                                            : { entries: g.jobs.map((j) => ({ stream: j.stream, entry_id: j.entry_id })) })}
                                                    />
                                                </TableCell>
                                            </TableRow>
                                            {expanded && g.jobs.map((j) => (
                                                <TableRow key={`${j.stream}:${j.entry_id}`} className="border-slate-100 bg-slate-50/40">
                                                    <TableCell className="py-2 pl-10 text-xs text-slate-700" colSpan={2}>
                                                        {jobLabel(j)}
                                                        <span className="text-slate-400"> · {j.browser || '—'}{j.device ? ` · ${j.device}` : ''}</span>
                                                    </TableCell>
                                                    <TableCell className="py-2 text-xs">
                                                        {j.claimed
                                                            ? <span className="text-indigo-700">on <code className="text-[10px]">{j.consumer}</code></span>
                                                            : <span className="text-slate-500">queued</span>}
                                                        <div className="text-[10px] text-slate-400"><code>{j.stream}</code></div>
                                                    </TableCell>
                                                    <TableCell className="py-2 text-xs text-slate-500" colSpan={2}>
                                                        {formatAge(j.waiting_seconds)}
                                                    </TableCell>
                                                </TableRow>
                                            ))}
                                        </Fragment>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </div>
                )}
            </div>
            {truncated && (
                <p className="text-xs text-slate-400 mt-2">
                    {total} jobs are waiting; the oldest {jobs.length} are listed. "Clear whole queue" covers all of them.
                </p>
            )}
        </section>
    );
}

export function DeadLetterPanel() {
    const qc = useQueryClient();
    const { data, error, isLoading } = useQuery({
        queryKey: ['dead-letter'], queryFn: fleetApi.deadLetters, refetchInterval: 15000, retry: false,
    });
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['dead-letter'] });
        qc.invalidateQueries({ queryKey: ['job-queue'] });
        qc.invalidateQueries({ queryKey: ['worker-fleet'] });
    };
    const replay = useMutation({
        mutationFn: fleetApi.replayDeadLetters,
        onSuccess: (r: { replayed_count: number }) => { toast.success(`${r.replayed_count} job(s) sent back to the queue`); refresh(); },
        onError: (e) => toast.error(`Retry failed: ${errDetail(e)}`),
    });
    const discard = useMutation({
        mutationFn: fleetApi.discardDeadLetters,
        onSuccess: () => { toast.success('Discarded'); refresh(); },
        onError: (e) => toast.error(`Discard failed: ${errDetail(e)}`),
    });
    const busy = replay.isPending || discard.isPending;
    const entries = data?.entries ?? [];

    return (
        <section className="mb-8">
            <div className="flex flex-wrap items-end gap-3 mb-3">
                <div>
                    <h2 className="text-lg font-bold text-slate-800">Failed jobs</h2>
                    <p className="text-xs text-slate-500">
                        Jobs that crashed a worker three times and were set aside. Retry sends them back to the queue.
                    </p>
                </div>
                {entries.length > 0 && (
                    <div className="ml-auto flex gap-2">
                        <Button variant="outline" size="sm" disabled={busy} onClick={() => replay.mutate({ replay_all: true })}>
                            <RotateCcw className="w-4 h-4 mr-1.5" /> Retry all
                        </Button>
                        <Confirm
                            trigger={
                                <Button variant="outline" size="sm" disabled={busy}
                                    className="text-rose-700 border-rose-200 hover:bg-rose-50">
                                    <Trash2 className="w-4 h-4 mr-1.5" /> Discard all
                                </Button>
                            }
                            title="Discard every failed job?"
                            body={<p>{data?.depth ?? entries.length} job(s) are deleted and cannot be retried later.</p>}
                            action="Discard all"
                            onConfirm={() => discard.mutate({ discard_all: true })}
                        />
                    </div>
                )}
            </div>
            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                {isLoading ? (
                    <div className="p-8 text-center text-sm text-slate-400">Loading…</div>
                ) : error ? (
                    <div className="p-8 text-center text-sm text-rose-600">Failed to load: {errDetail(error)}</div>
                ) : entries.length === 0 ? (
                    <div className="p-8 text-center text-sm text-slate-400">No failed jobs.</div>
                ) : (
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader className="bg-slate-50/50">
                                <TableRow className="border-slate-100 hover:bg-transparent">
                                    <TableHead className={TH}>Run</TableHead>
                                    <TableHead className={TH}>Case</TableHead>
                                    <TableHead className={TH}>Why it failed</TableHead>
                                    <TableHead className={TH}>Set aside</TableHead>
                                    <TableHead className={`${TH} text-right`}>Action</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {entries.map((e) => {
                                    const ms = Number(e.entry_id.split('-')[0]);
                                    return (
                                        <TableRow key={e.entry_id} className="border-slate-100">
                                            <TableCell className="py-3 text-xs">
                                                {e.run_id && e.run_id !== '0'
                                                    ? <Link to={`/runs/${e.run_id}`} className="text-indigo-600 hover:underline">run #{e.run_id}</Link>
                                                    : <span className="text-slate-400">—</span>}
                                            </TableCell>
                                            <TableCell className="py-3 text-xs">{e.test_case_id ? `#${e.test_case_id}` : '—'}</TableCell>
                                            <TableCell className="py-3 text-xs text-slate-600 max-w-[420px] break-words">{e.error || '—'}</TableCell>
                                            <TableCell className="py-3 text-xs text-slate-500 whitespace-nowrap">
                                                {Number.isFinite(ms) ? new Date(ms).toLocaleString() : "—"}
                                            </TableCell>
                                            <TableCell className="py-3 text-right whitespace-nowrap">
                                                <Button variant="ghost" size="sm" disabled={busy || !e.payload_present}
                                                    onClick={() => replay.mutate({ entry_ids: [e.entry_id] })}>
                                                    <RotateCcw className="w-4 h-4 mr-1" /> Retry
                                                </Button>
                                                <Button variant="ghost" size="sm" disabled={busy} className="text-rose-700 hover:bg-rose-50"
                                                    onClick={() => discard.mutate({ entry_ids: [e.entry_id] })}>
                                                    <Trash2 className="w-4 h-4 mr-1" /> Discard
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    </div>
                )}
            </div>
        </section>
    );
}
