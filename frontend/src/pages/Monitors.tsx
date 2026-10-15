import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNowStrict } from 'date-fns';
import { RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { monitorsApi, type MonitorStatus } from '@/api/monitors';
import {
    DetailCard, EmptyState, EvidenceList, KindChip, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Kind, Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const ago = (iso: string | null) => (iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : 'never');

const stateLook: Record<MonitorStatus['state'], { kind: Kind; tone: Tone; label: string }> = {
    down: { kind: 'monitor', tone: 'peach', label: 'Down' },
    up: { kind: 'pass', tone: 'mint', label: 'Up' },
    unknown: { kind: 'skip', tone: 'neutral', label: 'No checks yet' },
};

const fmtUptime = (v: number | null) => (v === null || v === undefined ? '—' : `${v}%`);

// Multi-region monitors (WP8): one pill per region; empty for unlabelled monitors.
function RegionPills({ regions }: { regions: NonNullable<MonitorStatus['regions']> }) {
    if (!regions.length) return null;
    return (
        <div className="flex flex-wrap gap-1.5" aria-label="Per-region status">
            {regions.map((r) => (
                <Pill
                    key={r.region}
                    tone={stateLook[r.state]?.tone ?? 'neutral'}
                >
                    <span title={`${r.region}: ${r.state}${r.consecutive_failures ? `, ${r.consecutive_failures} failing check(s)` : ''}${r.last_checked_at ? ` · last ${r.last_checked_at}` : ''}`}>
                        {r.region} · {r.state}{r.consecutive_failures ? ` (${r.consecutive_failures} in a row)` : ''}
                    </span>
                </Pill>
            ))}
        </div>
    );
}

function StatusPageCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const page = useQuery({
        queryKey: ['status-page', projectId],
        queryFn: () => monitorsApi.getStatusPage(projectId),
    });
    const save = useMutation({
        mutationFn: (body: Record<string, unknown>) => monitorsApi.upsertStatusPage(projectId, body),
        onSuccess: () => { toast.success('Status page updated'); qc.invalidateQueries({ queryKey: ['status-page', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const p = page.data;
    const publicUrl = p ? `${window.location.origin}/status/${p.slug}` : null;

    return (
        <div className="flex flex-col gap-3 rounded-2xl border border-border bg-white p-4">
            <div className="flex items-baseline justify-between gap-2">
                <h2 className="text-sm font-semibold text-ink">Public status page</h2>
                {p && <Pill tone={p.enabled ? 'mint' : 'neutral'}>{p.enabled ? 'Live' : 'Off'}</Pill>}
            </div>
            {p && p.enabled && publicUrl ? (
                <a href={publicUrl} target="_blank" rel="noreferrer" className="break-all font-mono text-xs text-ink-soft hover:text-ink">{publicUrl}</a>
            ) : (
                <p className="text-[13px] leading-relaxed text-ink-soft">An unauthenticated page built from this project's monitors. It shows names and uptime only.</p>
            )}
            <div className="flex flex-wrap gap-2">
                {!p ? (
                    <Button size="sm" onClick={() => save.mutate({ enabled: true })} disabled={save.isPending}>Create page</Button>
                ) : (
                    <>
                        {p.enabled && publicUrl && (
                            <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(publicUrl); toast.success('Link copied'); }}>Copy link</Button>
                        )}
                        <Button size="sm" variant="outline" onClick={() => save.mutate({ enabled: !p.enabled })} disabled={save.isPending}>
                            {p.enabled ? 'Turn off' : 'Turn on'}
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => save.mutate({ regenerate_slug: true })} disabled={save.isPending}>Rotate link</Button>
                    </>
                )}
            </div>
        </div>
    );
}

function CheckBars({ checks, large = false }: { checks: MonitorStatus['recent_checks']; large?: boolean }) {
    // Most-recent-first from the API; draw oldest -> newest, left -> right.
    const items = [...checks].reverse().slice(-30);
    const down = items.filter((c) => !c.is_up).length;
    if (!items.length) return <span className="text-xs text-ink-muted">No checks recorded yet</span>;
    return (
        <div
            role="img"
            aria-label={`${items.length - down} of the last ${items.length} checks were up`}
            className={cn('flex gap-[3px]', large ? 'h-11' : 'h-4')}
        >
            {items.map((c) => (
                <span
                    key={c.id}
                    title={`${new Date(c.checked_at).toLocaleString()}${c.region ? ` [${c.region}]` : ''}: ${c.status}`}
                    className={cn('flex-1 rounded-[4px]', c.is_up ? 'bg-[#8FD3B6]' : 'bg-[#E0603A]')}
                />
            ))}
        </div>
    );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
            <span className="text-xs text-ink-soft">{label}</span>
            <span className={cn('text-2xl font-bold tracking-tight text-ink tabular-nums', tone)}>{value}</span>
        </div>
    );
}

function meaning(m: MonitorStatus) {
    if (!m.is_active) return 'Paused. It records no checks and sends no alerts until the schedule is turned back on.';
    if (m.state === 'down') {
        return `${m.consecutive_failures} failed check${m.consecutive_failures === 1 ? '' : 's'} in a row. Test runs failing in the same window are likely the environment, not the code.`;
    }
    if (m.state === 'unknown') return 'No checks have been recorded yet. The first one runs on the schedule’s next tick.';
    if (m.uptime_7d !== null && m.uptime_7d < 99) return `Up now, but only ${m.uptime_7d}% of the last 7 days. Worth a look before it turns into a release blocker.`;
    return 'Healthy. Each check is a real test run on a timer, so it catches broken pages, not just a down server.';
}

export default function Monitors() {
    const projectId = useActiveProjectId();
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const { data: monitors, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['monitors', projectId],
        queryFn: () => monitorsApi.list(projectId!),
        enabled: !!projectId,
        refetchInterval: 30000,
    });

    const list = monitors ?? [];
    const groups = [
        { name: 'Needs attention', items: list.filter((m) => m.is_active && m.state === 'down') },
        { name: 'Healthy', items: list.filter((m) => m.is_active && m.state === 'up') },
        { name: 'No checks yet', items: list.filter((m) => m.is_active && m.state === 'unknown') },
        { name: 'Paused', items: list.filter((m) => !m.is_active) },
    ].filter((g) => g.items.length > 0);

    const selected = list.find((m) => m.schedule_id === selectedId) ?? groups[0]?.items[0] ?? null;
    const downCount = list.filter((m) => m.is_active && m.state === 'down').length;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Watch"
                subtitle={
                    !projectId ? 'Pick a project in the sidebar.'
                        : monitors ? `${list.length} monitor${list.length === 1 ? '' : 's'} · ${downCount} down · refreshes every 30 seconds`
                            : 'Production health checks on a timer.'
                }
                actions={
                    <>
                        <Button variant="outline" onClick={() => refetch()} aria-label="Refresh now">
                            <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
                            Refresh
                        </Button>
                        <Button asChild><Link to="/schedules">New monitor</Link></Button>
                    </>
                }
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its monitors.</EmptyState>
            ) : isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load monitors">{errDetail(error)}</EmptyState>
            ) : list.length === 0 ? (
                <div className="flex flex-col gap-4">
                    <EmptyState title="No monitors yet">
                        Turn on monitor mode for a schedule to make its recurring run a production health check, with uptime and failure-streak alerts.
                    </EmptyState>
                    <div className="max-w-[440px]"><StatusPageCard projectId={projectId} /></div>
                </div>
            ) : (
                <SplitView
                    list={
                        <>
                            {groups.map((g) => (
                                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                    {g.items.map((m) => (
                                        <ListRow
                                            key={m.schedule_id}
                                            selected={selected?.schedule_id === m.schedule_id}
                                            onSelect={() => setSelectedId(m.schedule_id)}
                                            leading={<KindChip kind={stateLook[m.state].kind} size="sm" />}
                                            title={m.name}
                                            meta={`${m.total_checks} checks · last ${ago(m.last_checked_at)}`}
                                            trailing={<span className="font-mono text-xs text-ink-soft">{fmtUptime(m.uptime_24h)}</span>}
                                        />
                                    ))}
                                </ListGroup>
                            ))}
                            <StatusPageCard projectId={projectId} />
                        </>
                    }
                    detail={selected && (
                        <DetailCard label="Selected monitor">
                            <div className="flex flex-col gap-2">
                                <Pill tone={selected.is_active ? stateLook[selected.state].tone : 'neutral'} className="self-start">
                                    {selected.is_active ? stateLook[selected.state].label : 'Paused'}
                                    {selected.state === 'down' && selected.consecutive_failures > 0 && ` · ${selected.consecutive_failures} in a row`}
                                </Pill>
                                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                                <span className="text-sm text-ink-soft">Schedule #{selected.schedule_id} · last checked {ago(selected.last_checked_at)}</span>
                                <RegionPills regions={selected.regions || []} />
                            </div>

                            <div className="flex flex-col gap-2">
                                <span className="flex justify-between text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                                    <span>Recent checks</span>
                                    <span className="font-medium normal-case tracking-normal">oldest to newest</span>
                                </span>
                                <CheckBars checks={selected.recent_checks} large />
                            </div>

                            <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3">
                                <Stat label="24-hour uptime" value={fmtUptime(selected.uptime_24h)} />
                                <Stat label="7-day uptime" value={fmtUptime(selected.uptime_7d)} />
                                <Stat label="Failures in a row" value={String(selected.consecutive_failures)} tone={selected.consecutive_failures ? 'text-pastel-ink-peach' : undefined} />
                                <Stat label="Checks recorded" value={String(selected.total_checks)} />
                            </div>

                            <Panel title="What this means" callout>{meaning(selected)}</Panel>

                            <EvidenceList
                                items={selected.recent_checks.slice(0, 5).map((c) => ({
                                    tag: c.is_up ? 'up' : 'down',
                                    text: `${c.status}${c.region ? ` · ${c.region}` : ''} · ${ago(c.checked_at)}`,
                                    to: c.run_id ? `/runs/${c.run_id}` : undefined,
                                }))}
                            />

                            <div className="flex flex-wrap gap-2.5">
                                {selected.recent_checks[0]?.run_id && (
                                    <Button asChild size="lg"><Link to={`/runs/${selected.recent_checks[0].run_id}`}>Open latest check</Link></Button>
                                )}
                                <Button asChild size="lg" variant="outline"><Link to="/schedules">Edit schedule</Link></Button>
                            </div>
                        </DetailCard>
                    )}
                />
            )}
        </div>
    );
}
