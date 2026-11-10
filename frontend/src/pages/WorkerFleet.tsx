import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';
import { MetricTile } from '@/components/reports/ReportControls';
import { Fact, FactGrid, SectionPanel } from '@/components/redesign-local/ops/bits';
import { tbl } from '@/components/redesign-local/ops/look';
import { fleetApi, type FleetSnapshot, type FleetWorker } from '@/api/fleet';
import { formatAge } from '@/lib/fleet';
import { DeadLetterPanel, JobQueuePanel } from '@/components/fleet/QueuePanels';

type ApiErr = { response?: { status?: number; data?: { detail?: unknown } }; message?: string };
const errDetail = (err: unknown): string => {
    const e = err as ApiErr | null;
    return String(e?.response?.data?.detail || e?.message || 'Unknown error');
};

type Tab = 'workers' | 'waiting' | 'failed' | 'streams';

function WorkerDetail({ w }: { w: FleetWorker }) {
    return (
        <DetailCard label={`Worker ${w.worker_id}`}>
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={w.alive ? 'mint' : 'peach'}>{w.alive ? 'Alive' : 'Lost'}</Pill>
                    {w.current_job && <Pill tone="lavender">Busy</Pill>}
                    {w.version_skew && <Pill tone="butter">Version skew</Pill>}
                </div>
                <h2 className="break-all font-mono text-[22px] font-bold leading-tight tracking-[-0.01em] text-ink">{w.worker_id}</h2>
                <span className="text-sm text-ink-soft">{w.hostname || '—'}</span>
            </div>

            <FactGrid>
                <Fact label="Kind">{w.kind}</Fact>
                <Fact label="Region">{w.region || 'global pool'}</Fact>
                {w.workspace_id !== null && w.workspace_id !== undefined && (
                    <Fact label="Workspace">workspace #{w.workspace_id}</Fact>
                )}
                <Fact label="Version">
                    {w.version || '—'}
                    {w.git_sha && <div className="font-mono text-xs text-ink-soft">{w.git_sha.slice(0, 10)}</div>}
                </Fact>
                <Fact label="Current job">
                    {w.current_job ? (
                        <Link to={`/runs/${w.current_job.run_id}`} className="font-semibold underline underline-offset-2">
                            run #{w.current_job.run_id}
                        </Link>
                    ) : <span className="text-ink-soft">idle</span>}
                </Fact>
                <Fact label="Processed / failed">
                    <span className="tabular-nums">
                        {w.jobs_processed} / <span className={w.jobs_failed ? 'font-semibold text-pastel-ink-peach' : ''}>{w.jobs_failed}</span>
                    </span>
                </Fact>
                <Fact label="Started">{w.started_at ? new Date(w.started_at).toLocaleString() : '—'}</Fact>
                <Fact label="Last seen">{formatAge(w.age_seconds)} ago</Fact>
            </FactGrid>

            {w.version_skew && (
                <div className="flex flex-col gap-2">
                    <Eyebrow right={<span>{w.missing_step_types.length} missing</span>}>Step types this worker lacks</Eyebrow>
                    <ul className="flex flex-wrap gap-1.5">
                        {w.missing_step_types.map((t) => (
                            <li key={t}><Pill tone="butter"><span className="font-mono">{t}</span></Pill></li>
                        ))}
                    </ul>
                    <p className="text-xs text-ink-soft">Older than the step catalog — rebuild the worker image.</p>
                </div>
            )}
            {!w.step_types_reported && w.alive && (
                <p className="text-xs text-ink-soft">This worker did not report its step types.</p>
            )}
        </DetailCard>
    );
}

function WorkersView({ workers }: { workers: FleetWorker[] }) {
    const [selected, setSelected] = useState<string | null>(null);
    if (workers.length === 0) {
        return (
            <EmptyState title="No workers reporting">
                No worker has reported a heartbeat. Workers built before this release do not heartbeat — rebuild the execution-worker image.
            </EmptyState>
        );
    }
    const groups = [
        { name: 'Lost', items: workers.filter((w) => !w.alive) },
        { name: 'Alive', items: workers.filter((w) => w.alive) },
    ].filter((g) => g.items.length > 0);
    const current = workers.find((w) => w.worker_id === selected) ?? groups[0].items[0];
    return (
        <SplitView
            list={groups.map((g) => (
                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                    {g.items.map((w) => (
                        <ListRow
                            key={w.worker_id}
                            selected={current.worker_id === w.worker_id}
                            onSelect={() => setSelected(w.worker_id)}
                            title={<span className="font-mono text-[13px]">{w.worker_id}</span>}
                            meta={<span>{w.kind} · {w.region || 'global pool'} · seen {formatAge(w.age_seconds)} ago</span>}
                            trailing={w.version_skew
                                ? <Pill tone="butter">Skew</Pill>
                                : w.current_job
                                    ? <Pill tone="lavender">Busy</Pill>
                                    : <span className="text-xs text-ink-soft">{w.alive ? 'idle' : 'lost'}</span>}
                        />
                    ))}
                </ListGroup>
            ))}
            detail={<WorkerDetail key={current.worker_id} w={current} />}
        />
    );
}

function StreamsView({ streams }: { streams: FleetSnapshot['streams'] }) {
    return (
        <SectionPanel
            flush
            title="Job streams"
            description="Backlog straight from the Redis streams, per stream and consumer group."
            footer={<>Length counts every entry still stored in the stream (including acknowledged ones); "queued" is what no worker has picked up yet.</>}
        >
            <div className="overflow-x-auto">
                <table className={tbl.table}>
                    <thead>
                        <tr className={tbl.headRow}>
                            <th className={tbl.th}>Stream</th>
                            <th className={tbl.th}>Region</th>
                            <th className={tbl.th}>Length</th>
                            <th className={tbl.th}>Queued (lag)</th>
                            <th className={tbl.th}>In flight</th>
                            <th className={tbl.th}>In flight per consumer</th>
                        </tr>
                    </thead>
                    <tbody>
                        {streams.map((s) => {
                            const busy = s.consumers.filter((c) => c.pending > 0);
                            return (
                                <tr key={s.stream} className={tbl.row}>
                                    <td className={tbl.td}>
                                        <code className="font-mono text-xs">{s.stream}</code>
                                        <div className="text-[11px] text-ink-soft">{s.group}</div>
                                    </td>
                                    <td className={tbl.td}>{s.region || 'global'}</td>
                                    <td className={`${tbl.td} tabular-nums`}>{s.length}</td>
                                    <td className={`${tbl.td} tabular-nums`}>
                                        {s.lag ? <Pill tone="butter">{s.lag}</Pill> : (s.lag ?? '—')}
                                    </td>
                                    <td className={`${tbl.td} tabular-nums`}>{s.pending}</td>
                                    <td className={tbl.td}>
                                        {busy.length === 0 ? (
                                            <span className="text-ink-soft">—</span>
                                        ) : (
                                            <div className="flex flex-wrap gap-1">
                                                {busy.map((c) => (
                                                    <code key={c.name} className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[11px]">
                                                        {c.name}: {c.pending}
                                                    </code>
                                                ))}
                                            </div>
                                        )}
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </SectionPanel>
    );
}

export default function WorkerFleet() {
    const [tab, setTab] = useState<Tab>('workers');
    const { data, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['worker-fleet'],
        queryFn: fleetApi.get,
        refetchInterval: 15000,
        retry: false,
    });
    const status = (error as ApiErr | null)?.response?.status;
    const ready = !isLoading && !error && !!data;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                crumbs={<span>Instance admin</span>}
                title="Worker fleet"
                subtitle={<>
                    Every execution worker heartbeats every {data?.heartbeat_interval_seconds ?? 15}s. A worker that misses its
                    heartbeat for {data?.heartbeat_ttl_seconds ?? 45}s is shown as lost. Job backlog comes straight from the Redis streams.
                </>}
                actions={
                    <Button variant="outline" onClick={() => refetch()}>
                        <RefreshCw className={isFetching ? 'animate-spin' : ''} /> Refresh
                    </Button>
                }
                toolbar={ready ? (
                    <PanelTabs<Tab>
                        label="Fleet sections"
                        value={tab}
                        onChange={setTab}
                        tabs={[
                            { id: 'workers', label: 'Workers', count: data.workers.length },
                            { id: 'waiting', label: 'Waiting jobs' },
                            { id: 'failed', label: 'Failed jobs', count: data.dead_letter_depth },
                            { id: 'streams', label: 'Job streams', count: data.streams.length },
                        ]}
                    />
                ) : undefined}
            />

            {isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : status === 403 ? (
                <EmptyState title="Instance admins only">
                    The worker fleet lists every worker host and region in this deployment, so it is only
                    visible to the instance administrator.
                </EmptyState>
            ) : error || !data ? (
                <EmptyState title="Failed to load the worker fleet">{errDetail(error)}</EmptyState>
            ) : (
                <>
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                        <MetricTile label="Alive" value={data.summary.alive} />
                        <MetricTile label="Lost" value={data.summary.lost}
                            pill={data.summary.lost ? <Pill tone="peach">Lost</Pill> : undefined}>
                            <span>missed heartbeat recently</span>
                        </MetricTile>
                        <MetricTile label="Busy" value={data.summary.busy}><span>running a job now</span></MetricTile>
                        <MetricTile label="Version skew" value={data.summary.skewed}
                            pill={data.summary.skewed ? <Pill tone="butter">Skew</Pill> : undefined}>
                            <span>older than step catalog</span>
                        </MetricTile>
                        <MetricTile label="Dead letters" value={data.dead_letter_depth}
                            pill={data.dead_letter_depth ? <Pill tone="peach">Failed</Pill> : undefined} />
                    </div>

                    {data.warnings.length > 0 && (
                        <div role="alert" className="flex flex-col gap-2 rounded-3xl border border-border bg-white p-5 shadow-[inset_4px_0_0_#F2C94C]">
                            <Eyebrow>Needs attention</Eyebrow>
                            <ul className="flex flex-col gap-1 text-sm text-ink">
                                {data.warnings.map((w) => <li key={w}>{w}</li>)}
                            </ul>
                        </div>
                    )}

                    {tab === 'workers' && <WorkersView workers={data.workers} />}
                    {tab === 'waiting' && <JobQueuePanel />}
                    {tab === 'failed' && <DeadLetterPanel />}
                    {tab === 'streams' && <StreamsView streams={data.streams} />}
                </>
            )}
        </div>
    );
}
