import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNowStrict } from 'date-fns';
import { RefreshCw, Ticket } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { triageApi, FailureCluster } from '@/api/triage';
import { ticketsApi } from '@/api/tickets';
import {
    DetailCard, EmptyState, EvidenceList, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const ago = (iso: string | null | undefined) => (iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : 'never');

const CATEGORY: Record<string, { label: string; tone: Tone; explain: string }> = {
    selector: { label: 'Selector', tone: 'lavender', explain: 'An element the test looks for could not be found. Usually the page changed, so the locator needs updating.' },
    timeout: { label: 'Timeout', tone: 'butter', explain: 'Something took longer than the test was willing to wait. Check for a slow backend or an environment problem before blaming the test.' },
    assertion: { label: 'Assertion', tone: 'neutral', explain: 'The page loaded but showed something different from what the test expects. This is the most likely kind to be a real bug.' },
    network: { label: 'Network', tone: 'peach', explain: 'A request failed or never got a response. Often the environment, not the code.' },
    navigation: { label: 'Navigation', tone: 'pink', explain: 'The browser could not reach or finish loading a page.' },
    other: { label: 'Other', tone: 'neutral', explain: 'These failures share the same error fingerprint but do not fit a known category.' },
};
const cat = (c: string) => CATEGORY[c] ?? { ...CATEGORY.other, label: c };

const STATUS: Record<FailureCluster['status'], { label: string; tone: Tone }> = {
    open: { label: 'Open', tone: 'peach' },
    investigating: { label: 'Investigating', tone: 'butter' },
    resolved: { label: 'Resolved', tone: 'mint' },
    ignored: { label: 'Ignored', tone: 'neutral' },
};
const STATUSES = ['open', 'investigating', 'resolved', 'ignored'];
const FILTERS = [...STATUSES, 'all'];

function Fact({ label, value }: { label: string; value: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
            <span className="text-xs text-ink-soft">{label}</span>
            <span className="text-xl font-bold tracking-tight tabular-nums text-ink">{value}</span>
        </div>
    );
}

function ClusterDetail({ cluster }: { cluster: FailureCluster }) {
    const qc = useQueryClient();
    const detail = useQuery({ queryKey: ['cluster', cluster.id], queryFn: () => triageApi.get(cluster.id) });

    // trackers for the one-ticket-per-cluster action
    const { data: workspaces } = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const wsId = workspaces?.[0]?.id ?? null;
    const trackers = useQuery({ queryKey: ['trackers', wsId], queryFn: () => ticketsApi.listConfigs(wsId!), enabled: !!wsId });
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

    const c = cat(cluster.category);
    const enabledTrackers = (trackers.data || []).filter((t) => t.enabled);

    return (
        <DetailCard label="Selected failure cluster">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-2">
                    <Pill tone={STATUS[cluster.status]?.tone ?? 'neutral'}>{STATUS[cluster.status]?.label ?? cluster.status}</Pill>
                    <Pill tone={c.tone}>{c.label}</Pill>
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{cluster.title}</h2>
                <span className="text-sm text-ink-soft">
                    Last seen {ago(cluster.last_seen_at)}{cluster.last_run_id ? ` in run #${cluster.last_run_id}` : ''}
                </span>
            </div>

            <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3">
                <Fact label="Times it failed" value={String(cluster.occurrence_count)} />
                <Fact label="First seen" value={ago(cluster.first_seen_at)} />
                <Fact label="Last seen" value={ago(cluster.last_seen_at)} />
            </div>

            <Panel title="What this means" callout>
                {cluster.occurrence_count === 1 ? 'One failure' : `${cluster.occurrence_count} failures`} share the same root cause, so fixing it once clears the group. {c.explain}
            </Panel>

            {cluster.sample_error && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Sample error</Eyebrow>
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-2xl border border-border bg-background p-4 font-mono text-xs leading-relaxed text-ink">{cluster.sample_error}</pre>
                </div>
            )}

            {!detail.data ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading occurrences">
                    {[0, 1].map((i) => <div key={i} className="h-12 animate-pulse rounded-2xl bg-background" />)}
                </div>
            ) : (
                <EvidenceList
                    items={detail.data.occurrences.map((o) => ({
                        tag: `run #${o.run_id}`,
                        text: o.test_name,
                        to: `/runs/${o.run_id}`,
                    }))}
                />
            )}

            <div className="flex flex-col gap-3 border-t border-border pt-5">
                <div className="flex flex-wrap items-end gap-3">
                    <label className="flex flex-col gap-1.5 text-[13px] text-ink-soft">
                        Status
                        <Select value={cluster.status} onValueChange={(v) => setStatus.mutate(v)}>
                            <SelectTrigger className="h-10 w-[170px] rounded-full bg-white text-ink" aria-label="Cluster status"><SelectValue /></SelectTrigger>
                            <SelectContent>{STATUSES.map((st) => <SelectItem key={st} value={st}>{STATUS[st as FailureCluster['status']].label}</SelectItem>)}</SelectContent>
                        </Select>
                    </label>
                    <label className="flex min-w-[200px] flex-1 flex-col gap-1.5 text-[13px] text-ink-soft sm:max-w-xs">
                        File one ticket for this whole group
                        <Select value={trackerId} onValueChange={setTrackerId}>
                            <SelectTrigger className="h-10 rounded-full bg-white text-ink" aria-label="Issue tracker"><SelectValue placeholder="Select tracker" /></SelectTrigger>
                            <SelectContent>{enabledTrackers.map((t) => <SelectItem key={t.id} value={t.id.toString()}>{t.name} ({t.provider})</SelectItem>)}</SelectContent>
                        </Select>
                    </label>
                    <Button variant="outline" disabled={!trackerId || fileTicket.isPending} onClick={() => fileTicket.mutate()}>
                        <Ticket className="h-4 w-4" /> Create ticket
                    </Button>
                </div>
                <div className="flex flex-wrap gap-2.5">
                    {cluster.last_run_id && (
                        <Button asChild size="lg"><Link to={`/runs/${cluster.last_run_id}`}>Open latest failing run</Link></Button>
                    )}
                </div>
            </div>
        </DetailCard>
    );
}

export default function Triage() {
    const projectId = useActiveProjectId();
    const [statusFilter, setStatusFilter] = useState('open');
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const clusters = useQuery({
        queryKey: ['clusters', projectId, statusFilter],
        queryFn: () => triageApi.list(projectId!, statusFilter === 'all' ? undefined : statusFilter),
        enabled: !!projectId,
    });

    // Group by root-cause category; the biggest groups (by total failures) first,
    // and the most frequent cluster first within each group.
    const list = clusters.data ?? [];
    const byCat = new Map<string, FailureCluster[]>();
    for (const c of [...list].sort((a, b) => b.occurrence_count - a.occurrence_count)) {
        byCat.set(c.category, [...(byCat.get(c.category) ?? []), c]);
    }
    const groups = [...byCat.entries()]
        .map(([category, items]) => ({ category, items, total: items.reduce((n, c) => n + c.occurrence_count, 0) }))
        .sort((a, b) => b.total - a.total);
    const top = [...list].sort((a, b) => b.occurrence_count - a.occurrence_count)[0] ?? null;
    const selected = list.find((c) => c.id === selectedId) ?? top;
    const totalFailures = list.reduce((n, c) => n + c.occurrence_count, 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Triage"
                subtitle={
                    !projectId ? 'Pick a project in the sidebar.'
                        : clusters.data ? `${list.length} problem${list.length === 1 ? '' : 's'} behind ${totalFailures} failure${totalFailures === 1 ? '' : 's'}. Fix one, and every failure in its group goes away.`
                            : 'Failures grouped by root cause, so one problem is one item.'
                }
                actions={projectId ? (
                    <Button variant="outline" onClick={() => clusters.refetch()} aria-label="Refresh clusters">
                        <RefreshCw className={cn('h-4 w-4', clusters.isFetching && 'animate-spin')} /> Refresh
                    </Button>
                ) : undefined}
            />

            {projectId && (
                <div role="tablist" aria-label="Filter by status" className="flex flex-wrap gap-1.5">
                    {FILTERS.map((f) => (
                        <button
                            key={f}
                            type="button"
                            role="tab"
                            aria-selected={statusFilter === f}
                            onClick={() => setStatusFilter(f)}
                            className={cn(
                                'min-h-10 rounded-full border px-4 text-sm font-semibold transition-colors',
                                statusFilter === f ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-secondary'
                            )}
                        >
                            {f === 'all' ? 'All' : STATUS[f as FailureCluster['status']].label}
                        </button>
                    ))}
                </div>
            )}

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its failure clusters.</EmptyState>
            ) : clusters.isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : clusters.error ? (
                <EmptyState title="Couldn't load failure clusters">{errDetail(clusters.error)}</EmptyState>
            ) : list.length === 0 ? (
                <EmptyState title={`No ${statusFilter === 'all' ? '' : `${STATUS[statusFilter as FailureCluster['status']].label.toLowerCase()} `}failure clusters`}>
                    Failing runs are grouped automatically as they finish.
                </EmptyState>
            ) : (
                <SplitView
                    list={groups.map((g) => (
                        <ListGroup key={g.category} title={cat(g.category).label} count={g.items.length}>
                            {g.items.map((c) => (
                                <ListRow
                                    key={c.id}
                                    selected={selected?.id === c.id}
                                    onSelect={() => setSelectedId(c.id)}
                                    leading={<KindChip kind={c.status === 'resolved' ? 'pass' : c.status === 'ignored' ? 'skip' : 'failure'} size="sm" />}
                                    title={<span className="line-clamp-2 break-words">{c.title}</span>}
                                    meta={`${STATUS[c.status]?.label ?? c.status} · last seen ${ago(c.last_seen_at)}`}
                                    trailing={<span className="font-mono text-[13px] tabular-nums text-ink" title="Times it failed">×{c.occurrence_count}</span>}
                                />
                            ))}
                        </ListGroup>
                    ))}
                    detail={selected && <ClusterDetail key={selected.id} cluster={selected} />}
                />
            )}
        </div>
    );
}
