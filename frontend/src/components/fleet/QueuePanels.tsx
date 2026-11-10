import { Fragment, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { toast } from 'sonner';
import { ChevronDown, ChevronRight, RotateCcw, Trash2, XCircle } from 'lucide-react';
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
    AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import { PanelNote, SectionPanel } from '@/components/redesign-local/ops/bits';
import { dangerOutline, tbl } from '@/components/redesign-local/ops/look';
import { fleetApi, type ClearQueueRequest, type ClearQueueResult, type QueuedJob } from '@/api/fleet';
import { formatAge, groupQueueByRun } from '@/lib/fleet';

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
                    <AlertDialogAction onClick={onConfirm} className="bg-pastel-ink-peach text-white hover:bg-pastel-ink-peach/90">{action}</AlertDialogAction>
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
        <SectionPanel
            flush
            title="Waiting jobs"
            description="Jobs no worker has finished yet. Clearing a job cancels its whole run."
            actions={total > 0 && (
                <Confirm
                    trigger={
                        <Button variant="outline" size="sm" disabled={clear.isPending} className={dangerOutline}>
                            <Trash2 /> Clear whole queue
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
            footer={truncated
                ? <>{total} jobs are waiting; the oldest {jobs.length} are listed. "Clear whole queue" covers all of them.</>
                : undefined}
        >
            {isLoading ? (
                <PanelNote>Loading…</PanelNote>
            ) : error ? (
                <PanelNote tone="error">Failed to load the queue: {errDetail(error)}</PanelNote>
            ) : groups.length === 0 ? (
                <PanelNote>Nothing is waiting. Every queued job has been picked up and finished.</PanelNote>
            ) : (
                <div className="overflow-x-auto">
                    <table className={tbl.table}>
                        <thead>
                            <tr className={tbl.headRow}>
                                <th className={tbl.th}>Run</th>
                                <th className={tbl.th}>Project / suite</th>
                                <th className={tbl.th}>Jobs</th>
                                <th className={tbl.th}>Oldest waiting</th>
                                <th className={`${tbl.th} text-right`}>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {groups.map((g) => {
                                const key = String(g.runId ?? 'side');
                                const run = g.runId !== null ? data?.runs[String(g.runId)] : undefined;
                                const expanded = !!open[key];
                                return (
                                    <Fragment key={key}>
                                        <tr className={tbl.row}>
                                            <td className={tbl.td}>
                                                <div className="flex flex-wrap items-center gap-2">
                                                    {g.runId !== null ? (
                                                        <Link to={`/runs/${g.runId}`} className="font-semibold text-ink underline-offset-2 hover:underline">run #{g.runId}</Link>
                                                    ) : <span className="font-semibold text-ink">Background checks</span>}
                                                    {run && <Pill tone="neutral" className="capitalize">{run.status.toLowerCase()}</Pill>}
                                                </div>
                                                <button type="button" aria-expanded={expanded}
                                                    onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}
                                                    className="mt-1 inline-flex items-center gap-1 text-xs font-semibold text-ink-soft hover:text-ink">
                                                    {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                                                    {expanded ? 'Hide jobs' : 'Show jobs'}
                                                </button>
                                            </td>
                                            <td className={tbl.td}>
                                                {g.runId === null ? (
                                                    <span className="text-ink-soft">crawls, page checks, persona refreshes</span>
                                                ) : run ? (
                                                    <>
                                                        <div className="font-semibold">{run.project_name || '—'}</div>
                                                        <div className="text-ink-soft">{run.suite_name || run.test_case_name || ''}</div>
                                                    </>
                                                ) : <span className="text-ink-soft">run not found</span>}
                                            </td>
                                            <td className={`${tbl.td} tabular-nums`}>
                                                {g.jobs.length} waiting
                                                {g.claimed > 0 && <div className="text-ink-soft">{g.claimed} on a worker</div>}
                                            </td>
                                            <td className={`${tbl.td} whitespace-nowrap text-ink-soft`}>
                                                {formatAge(g.oldestWaitingSeconds)}
                                            </td>
                                            <td className={`${tbl.td} text-right`}>
                                                <Confirm
                                                    trigger={
                                                        <Button variant="outline" size="sm" disabled={clear.isPending} className={dangerOutline}>
                                                            <XCircle />
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
                                            </td>
                                        </tr>
                                        {expanded && g.jobs.map((j) => (
                                            <tr key={`${j.stream}:${j.entry_id}`} className={`${tbl.row} bg-background`}>
                                                <td className={`${tbl.td} pl-10 sm:pl-12`} colSpan={2}>
                                                    {jobLabel(j)}
                                                    <span className="text-ink-soft"> · {j.browser || '—'}{j.device ? ` · ${j.device}` : ''}</span>
                                                </td>
                                                <td className={tbl.td}>
                                                    {j.claimed
                                                        ? <span>on <code className="font-mono text-xs">{j.consumer}</code></span>
                                                        : <span className="text-ink-soft">queued</span>}
                                                    <div className="font-mono text-[11px] text-ink-soft">{j.stream}</div>
                                                </td>
                                                <td className={`${tbl.td} text-ink-soft`} colSpan={2}>
                                                    {formatAge(j.waiting_seconds)}
                                                </td>
                                            </tr>
                                        ))}
                                    </Fragment>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </SectionPanel>
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
        <SectionPanel
            flush
            title="Failed jobs"
            description="Jobs that crashed a worker three times and were set aside. Retry sends them back to the queue."
            actions={entries.length > 0 && (
                <>
                    <Button size="sm" disabled={busy} onClick={() => replay.mutate({ replay_all: true })}>
                        <RotateCcw /> Retry all
                    </Button>
                    <Confirm
                        trigger={
                            <Button variant="outline" size="sm" disabled={busy} className={dangerOutline}>
                                <Trash2 /> Discard all
                            </Button>
                        }
                        title="Discard every failed job?"
                        body={<p>{data?.depth ?? entries.length} job(s) are deleted and cannot be retried later.</p>}
                        action="Discard all"
                        onConfirm={() => discard.mutate({ discard_all: true })}
                    />
                </>
            )}
        >
            {isLoading ? (
                <PanelNote>Loading…</PanelNote>
            ) : error ? (
                <PanelNote tone="error">Failed to load: {errDetail(error)}</PanelNote>
            ) : entries.length === 0 ? (
                <PanelNote>No failed jobs.</PanelNote>
            ) : (
                <div className="overflow-x-auto">
                    <table className={tbl.table}>
                        <thead>
                            <tr className={tbl.headRow}>
                                <th className={tbl.th}>Run</th>
                                <th className={tbl.th}>Case</th>
                                <th className={tbl.th}>Why it failed</th>
                                <th className={tbl.th}>Set aside</th>
                                <th className={`${tbl.th} text-right`}>Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {entries.map((e) => {
                                const ms = Number(e.entry_id.split('-')[0]);
                                return (
                                    <tr key={e.entry_id} className={tbl.row}>
                                        <td className={tbl.td}>
                                            {e.run_id && e.run_id !== '0'
                                                ? <Link to={`/runs/${e.run_id}`} className="font-semibold underline-offset-2 hover:underline">run #{e.run_id}</Link>
                                                : <span className="text-ink-soft">—</span>}
                                        </td>
                                        <td className={tbl.td}>{e.test_case_id ? `#${e.test_case_id}` : '—'}</td>
                                        <td className={`${tbl.td} max-w-[420px] break-words text-ink-soft`}>{e.error || '—'}</td>
                                        <td className={`${tbl.td} whitespace-nowrap text-ink-soft`}>
                                            {Number.isFinite(ms) ? new Date(ms).toLocaleString() : '—'}
                                        </td>
                                        <td className={`${tbl.td} whitespace-nowrap text-right`}>
                                            <div className="flex justify-end gap-1.5">
                                                <Button variant="outline" size="sm" disabled={busy || !e.payload_present}
                                                    onClick={() => replay.mutate({ entry_ids: [e.entry_id] })}>
                                                    <RotateCcw /> Retry
                                                </Button>
                                                <Button variant="outline" size="sm" disabled={busy} className={dangerOutline}
                                                    onClick={() => discard.mutate({ entry_ids: [e.entry_id] })}>
                                                    <Trash2 /> Discard
                                                </Button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}
        </SectionPanel>
    );
}
