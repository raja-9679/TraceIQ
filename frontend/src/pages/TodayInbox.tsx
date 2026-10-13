import { useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNowStrict } from 'date-fns';
import { Check } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { api, type TestRun } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { triageApi, type FailureCluster } from '@/api/triage';
import { caseProposalsApi, healProposalsApi, type CaseProposal, type HealProposal } from '@/api/proposals';
import { flakesApi, type FlakinessEntry } from '@/api/flakes';
import { monitorsApi, type MonitorStatus } from '@/api/monitors';
import { securityApi, type SecurityFinding } from '@/api/security';
import {
    DetailCard, EmptyState, EvidenceList, KindChip, ListGroup, ListRow, PageHeader, Panel, SplitView,
} from '@/components/redesign/primitives';
import { kinds, toneClasses, type Kind } from '@/components/redesign/kinds';

type Action = { label: string; to?: string; onClick?: () => void; busy?: boolean };

type Item = {
    key: string;
    kind: Kind;
    group: 'block' | 'review' | 'later';
    title: string;
    when: string | null;
    happened: ReactNode;
    read?: ReactNode;
    confidence?: string;
    evidence: { tag: string; text: ReactNode; to?: string }[];
    primary: Action;
    secondary?: Action;
    handled?: Action;
};

const groupNames: Record<Item['group'], string> = {
    block: 'Blocking',
    review: 'Needs your review',
    later: 'Worth a look',
};

const ago = (iso: string | null | undefined) =>
    iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : null;

const pct = (x: number) => `${Math.round(x * 100)}%`;

// First line of an error, trimmed to something that fits a list row.
const oneLine = (text: string | null | undefined, max = 120) => {
    const line = (text ?? '').split('\n')[0].trim();
    return line.length > max ? `${line.slice(0, max - 1)}…` : line;
};

function sinceLabel(item: Item) {
    return item.when ?? '';
}

export default function TodayInbox() {
    const { user } = useAuth();
    const projectId = useActiveProjectId();
    const qc = useQueryClient();
    const [selectedKey, setSelectedKey] = useState<string | null>(null);
    const enabled = projectId != null;

    // Each source is independent: one failing (say, a 403 on a feature the
    // user can't see) must not blank the whole inbox.
    const clusters = useQuery({ queryKey: ['inbox', 'clusters', projectId], queryFn: () => triageApi.list(projectId!, 'open'), enabled });
    const proposals = useQuery({ queryKey: ['inbox', 'proposals', projectId], queryFn: () => caseProposalsApi.list(projectId!, 'pending'), enabled });
    const heals = useQuery({ queryKey: ['inbox', 'heals'], queryFn: () => healProposalsApi.list('pending') });
    const flaky = useQuery({ queryKey: ['inbox', 'flaky', projectId], queryFn: () => flakesApi.projectFlakiness(projectId!, 14), enabled });
    const monitors = useQuery({ queryKey: ['inbox', 'monitors', projectId], queryFn: () => monitorsApi.list(projectId!), enabled });
    const scans = useQuery({ queryKey: ['inbox', 'scans', projectId], queryFn: () => securityApi.listScans(projectId!), enabled });
    const runs = useQuery({
        queryKey: ['inbox', 'runs', projectId],
        queryFn: async () => (await api.get(`/runs?project_id=${projectId}&limit=100`)).data.runs as TestRun[],
        enabled,
    });

    const invalidate = () => qc.invalidateQueries({ queryKey: ['inbox'] });
    const onError = (e: unknown) => {
        const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        toast.error(typeof detail === 'string' ? detail : 'That did not work. Try again.');
    };
    const clusterUpdate = useMutation({
        mutationFn: ({ id, status }: { id: number; status: FailureCluster['status'] }) => triageApi.update(id, { status }),
        onSuccess: invalidate, onError,
    });
    const healDecide = useMutation({
        mutationFn: ({ id, accept }: { id: number; accept: boolean }) => (accept ? healProposalsApi.accept(id) : healProposalsApi.reject(id)),
        onSuccess: (_d, v) => { toast.success(v.accept ? 'Selector repair applied' : 'Selector repair dismissed'); invalidate(); },
        onError,
    });
    const findingUpdate = useMutation({
        mutationFn: ({ id, status }: { id: number; status: string }) => securityApi.updateFinding(id, { status }),
        onSuccess: invalidate, onError,
    });

    const items = useMemo<Item[]>(() => {
        const out: Item[] = [];

        // Clusters still "open" but quiet for two weeks are history, not a
        // blocker. Titles are normalised signatures ("Step <str> : …"); the
        // sample error reads better when there is one.
        const staleBefore = clusters.dataUpdatedAt - 14 * 24 * 3600 * 1000;
        const vagueCategory = new Set(['other', 'unknown', 'uncategorized', '']);
        (clusters.data ?? []).forEach((c: FailureCluster) => out.push({
            key: `cluster-${c.id}`,
            kind: 'failure',
            group: new Date(c.last_seen_at).getTime() < staleBefore ? 'later' : 'block',
            title: oneLine(c.sample_error) || c.title,
            when: ago(c.last_seen_at),
            happened: <>Failed {c.occurrence_count} time{c.occurrence_count === 1 ? '' : 's'}, first {ago(c.first_seen_at)}. Grouped by matching error.</>,
            read: c.category && !vagueCategory.has(c.category) ? <>Looks like a <strong>{c.category.replace(/_/g, ' ')}</strong> problem.</> : undefined,
            evidence: [
                ...(c.sample_error ? [{ tag: 'error', text: <span className="font-mono text-[13px]">{c.sample_error}</span> }] : []),
                ...(c.last_run_id ? [{ tag: 'run', text: `Latest failing run #${c.last_run_id}`, to: `/runs/${c.last_run_id}` }] : []),
                { tag: 'cluster', text: `${c.occurrence_count} occurrences`, to: '/triage' },
            ],
            primary: c.last_run_id ? { label: 'Open run', to: `/runs/${c.last_run_id}` } : { label: 'Open in triage', to: '/triage' },
            secondary: c.status === 'open' ? { label: 'Mark investigating', onClick: () => clusterUpdate.mutate({ id: c.id, status: 'investigating' }) } : undefined,
            handled: { label: 'Resolve', onClick: () => clusterUpdate.mutate({ id: c.id, status: 'resolved' }) },
        }));

        // Failures that never became a cluster (clustering is per-result and
        // can lag or skip) still need a person: any suite whose latest run in
        // the last two weeks failed shows up here, unless a cluster above
        // already points at that run.
        const clusteredRuns = new Set((clusters.data ?? []).map((c) => c.last_run_id).filter(Boolean));
        const cutoff = runs.dataUpdatedAt - 14 * 24 * 3600 * 1000;
        const latestBySuite = new Map<string, TestRun>();
        (runs.data ?? []).forEach((r) => {
            const key = `${r.test_suite_id}:${r.test_case_name ?? ''}`;
            if (!latestBySuite.has(key)) latestBySuite.set(key, r); // API returns newest first
        });
        [...latestBySuite.values()]
            .filter((r) => (r.status === 'failed' || r.status === 'error') && new Date(r.created_at).getTime() > cutoff && !clusteredRuns.has(r.id))
            .forEach((r) => out.push({
                key: `run-${r.id}`,
                kind: 'failure',
                group: 'block',
                title: `${r.test_case_name || r.suite_name || `Suite ${r.test_suite_id}`} failed`,
                when: ago(r.created_at),
                happened: r.total_tests
                    ? <>{r.failed_tests} of {r.total_tests} test{r.total_tests === 1 ? '' : 's'} failed in run #{r.id}{r.suite_name && r.test_case_name ? <> of {r.suite_name}</> : null}.</>
                    : <>Run #{r.id} ended with status {r.status}.</>,
                read: r.ai_analysis ? <>{r.ai_analysis.length > 280 ? `${r.ai_analysis.slice(0, 280)}…` : r.ai_analysis}</> : undefined,
                evidence: [
                    ...(r.error_message ? [{ tag: 'error', text: <span className="font-mono text-[13px]">{r.error_message}</span> }] : []),
                    { tag: 'run', text: `Run #${r.id}`, to: `/runs/${r.id}` },
                ],
                primary: { label: 'Open run', to: `/runs/${r.id}` },
            }));

        (monitors.data ?? []).filter((m: MonitorStatus) => m.is_active && m.state === 'down').forEach((m) => out.push({
            key: `monitor-${m.schedule_id}`,
            kind: 'monitor',
            group: 'block',
            title: `${m.name} is down`,
            when: ago(m.last_checked_at),
            happened: <>{m.consecutive_failures} failed check{m.consecutive_failures === 1 ? '' : 's'} in a row.</>,
            read: m.uptime_24h != null ? <>Up {pct(m.uptime_24h)} of the last 24 hours. Runs failing in this window may be the environment, not the code.</> : undefined,
            evidence: m.recent_checks.slice(0, 3).map((ch) => ({
                tag: ch.is_up ? 'up' : 'down',
                text: `${ch.status} · ${ago(ch.checked_at)}`,
                to: ch.run_id ? `/runs/${ch.run_id}` : undefined,
            })),
            primary: { label: 'Open Watch', to: '/monitors' },
        }));

        const latestScan = (scans.data ?? []).filter((s) => s.status === 'completed')
            .sort((a, b) => (a.created_at < b.created_at ? 1 : -1))[0];
        (latestScan?.findings ?? [])
            .filter((f: SecurityFinding) => (f.status ?? 'open') === 'open' && (f.severity === 'high' || f.severity === 'medium'))
            .forEach((f) => out.push({
                key: `finding-${f.id}`,
                kind: 'security',
                group: f.severity === 'high' ? 'block' : 'later',
                title: f.title,
                when: ago(f.created_at),
                happened: f.description || `${f.severity} finding from the ${f.scan_type} scan.`,
                read: <>Severity <strong>{f.severity}</strong>{f.target_url ? <> on <span className="font-mono text-[13px]">{f.target_url}</span></> : null}.</>,
                evidence: f.evidence ? [{ tag: 'evidence', text: <span className="whitespace-pre-wrap font-mono text-[13px]">{f.evidence}</span> }] : [],
                primary: { label: 'Open Security', to: '/security' },
                secondary: { label: 'Acknowledge', onClick: () => findingUpdate.mutate({ id: f.id, status: 'acknowledged' }) },
                handled: { label: 'Resolve', onClick: () => findingUpdate.mutate({ id: f.id, status: 'resolved' }) },
            }));

        (proposals.data ?? []).forEach((p: CaseProposal) => {
            const name = p.payload?.name;
            const verb = { create: 'add', update: 'change', delete: 'delete', move: 'move' }[p.action];
            out.push({
                key: `proposal-${p.id}`,
                kind: 'review',
                group: 'review',
                title: `${p.agent_id || 'An agent'} proposed to ${verb} ${name ? `“${name}”` : 'a test'}`,
                when: ago(p.created_at),
                happened: p.rationale || `A ${p.action} proposal is waiting for a person to decide.`,
                read: <>Accepting needs a person other than whoever proposed it, when your workspace requires it.</>,
                confidence: `${pct(p.ai_confidence)} sure`,
                evidence: [
                    ...(p.payload?.steps?.length ? [{ tag: 'steps', text: `${p.payload.steps.length} steps` }] : []),
                    ...(p.source_run_id ? [{ tag: 'run', text: `From run #${p.source_run_id}`, to: `/runs/${p.source_run_id}` }] : []),
                ],
                primary: { label: 'Review', to: '/proposals' },
            });
        });

        (heals.data ?? []).forEach((h: HealProposal) => out.push({
            key: `heal-${h.id}`,
            kind: 'review',
            group: 'review',
            title: `Selector repair suggested${h.intent ? ` for “${h.intent}”` : ''}`,
            when: ago(h.created_at),
            happened: h.rationale || 'The old selector stopped matching; a replacement was found.',
            read: <>Only action steps are repaired. Assertions are never changed automatically.</>,
            confidence: `${pct(h.confidence)} sure`,
            evidence: [
                { tag: 'old', text: <span className="font-mono text-[13px]">{h.old_selector || '—'}</span> },
                { tag: 'new', text: <span className="font-mono text-[13px]">{h.new_selector}</span> },
                ...(h.source_run_id ? [{ tag: 'run', text: `Found in run #${h.source_run_id}`, to: `/runs/${h.source_run_id}` }] : []),
            ],
            primary: { label: 'Apply repair', onClick: () => healDecide.mutate({ id: h.id, accept: true }), busy: healDecide.isPending },
            secondary: { label: 'Dismiss', onClick: () => healDecide.mutate({ id: h.id, accept: false }) },
        }));

        (flaky.data ?? []).filter((f: FlakinessEntry) => f.flake_score >= 0.2).forEach((f) => out.push({
            key: `flaky-${f.test_case_id}`,
            kind: 'flaky',
            group: 'later',
            title: `${f.name} passes and fails at random`,
            when: null,
            happened: <>{f.recent_failures} recent failure{f.recent_failures === 1 ? '' : 's'} across {f.sample_count} runs.{f.is_quarantined ? ' Quarantined, so it no longer blocks releases.' : ''}</>,
            read: f.last_failure_message ? <>Last failure: <span className="font-mono text-[13px]">{f.last_failure_message}</span></> : undefined,
            confidence: `flake score ${pct(f.flake_score)}`,
            evidence: [],
            primary: { label: 'Open flaky tests', to: '/flaky-tests' },
        }));

        return out;
    }, [clusters.data, clusters.dataUpdatedAt, runs.data, runs.dataUpdatedAt, monitors.data, scans.data, proposals.data, heals.data, flaky.data, clusterUpdate, findingUpdate, healDecide]);

    const groups = (['block', 'review', 'later'] as const)
        .map((g) => ({ id: g, items: items.filter((i) => i.group === g) }))
        .filter((g) => g.items.length > 0);

    const selected = items.find((i) => i.key === selectedKey) ?? groups[0]?.items[0] ?? null;
    const loading = [clusters, runs, proposals, heals, flaky, monitors, scans].some((q) => q.isLoading);
    const failedSources = [
        ['failures', clusters], ['runs', runs], ['proposals', proposals], ['selector repairs', heals],
        ['flaky tests', flaky], ['monitors', monitors], ['security', scans],
    ].filter(([, q]) => (q as { isError: boolean }).isError).map(([n]) => n as string);

    const firstName = user?.full_name?.split(' ')[0];

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Today"
                subtitle={
                    loading ? 'Gathering everything that needs you…'
                        : `${items.length} to decide${firstName ? `, ${firstName}` : ''}. Everything else TraceIQ handled on its own.`
                }
                actions={<Button asChild><Link to="/suites">Run a suite</Link></Button>}
            />

            {failedSources.length > 0 && (
                <p className="rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink-soft">
                    Couldn't load {failedSources.join(', ')}. The rest of the inbox is shown.
                </p>
            )}

            {projectId == null ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see what needs you.</EmptyState>
            ) : loading && items.length === 0 ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2, 3].map((i) => (
                        <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />
                    ))}
                </div>
            ) : items.length === 0 ? (
                <EmptyState title="Inbox zero">Nothing needs a decision right now. New failures, proposals and findings will show up here.</EmptyState>
            ) : (
                <SplitView
                    list={groups.map((g) => (
                        <ListGroup key={g.id} title={groupNames[g.id]} count={g.items.length}>
                            {g.items.map((it) => (
                                <ListRow
                                    key={it.key}
                                    selected={selected?.key === it.key}
                                    onSelect={() => setSelectedKey(it.key)}
                                    leading={<KindChip kind={it.kind} />}
                                    title={it.title}
                                    meta={[kinds[it.kind].label, sinceLabel(it)].filter(Boolean).join(' · ')}
                                />
                            ))}
                        </ListGroup>
                    ))}
                    detail={selected && <InboxDetail item={selected} />}
                />
            )}
        </div>
    );
}

function ActionButton({ action, variant }: { action: Action; variant: 'default' | 'outline' }) {
    if (action.to) {
        return <Button asChild variant={variant} size="lg"><Link to={action.to}>{action.label}</Link></Button>;
    }
    return (
        <Button variant={variant} size="lg" onClick={action.onClick} disabled={action.busy}>
            {action.label}
        </Button>
    );
}

function InboxDetail({ item }: { item: Item }) {
    const k = kinds[item.kind];
    return (
        <DetailCard label="Selected decision">
            <div className="flex flex-wrap items-center gap-2.5">
                <KindChip kind={item.kind} />
                <span className={`text-[13px] font-bold ${toneClasses[k.tone].ink}`}>{k.label}</span>
                {item.when && <span className="text-[13px] text-ink-soft">{item.when}</span>}
            </div>
            <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{item.title}</h2>

            <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                <Panel title="What happened">{item.happened}</Panel>
                {item.read && <Panel title="TraceIQ thinks" right={item.confidence} callout>{item.read}</Panel>}
            </div>

            <EvidenceList items={item.evidence} />

            <div className="flex flex-wrap gap-2.5 pt-1">
                <ActionButton action={item.primary} variant="default" />
                {item.secondary && <ActionButton action={item.secondary} variant="outline" />}
                {item.handled && (
                    <Button variant="outline" size="lg" className="sm:ml-auto" onClick={item.handled.onClick}>
                        <Check className="h-4 w-4" strokeWidth={2.4} />
                        {item.handled.label}
                    </Button>
                )}
            </div>
        </DetailCard>
    );
}
