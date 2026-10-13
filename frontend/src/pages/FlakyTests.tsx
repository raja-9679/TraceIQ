import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { ShieldAlert, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { flakesApi, FlakinessEntry } from '@/api/flakes';
import {
    DetailCard, EmptyState, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';

interface ApiError extends Error {
    response?: { data?: { detail?: string } };
}

const errorDetail = (err: unknown): string => {
    const e = err as ApiError;
    return e.response?.data?.detail || e.message || 'Unknown error';
};

const pctOf = (score: number) => Math.round(Math.max(0, Math.min(1, score)) * 100);
const barColour = (score: number) => (score >= 0.66 ? 'bg-[#E0603A]' : score >= 0.33 ? 'bg-[#F2C94C]' : 'bg-[#8FD3B6]');

// ---------------------------------------------------------------------------
// Flake score bar — 0.0 stable → 1.0 pure flake
// ---------------------------------------------------------------------------

function FlakeScoreBar({ score, large = false }: { score: number; large?: boolean }) {
    const pct = pctOf(score);
    return (
        <span
            role="img"
            aria-label={`Flake score ${pct}%`}
            className={cn('block w-full overflow-hidden rounded-full bg-secondary', large ? 'h-3' : 'h-1.5')}
        >
            <span className={cn('block h-full rounded-full transition-all', barColour(score))} style={{ width: `${pct}%` }} />
        </span>
    );
}

function Fact({ label, value }: { label: string; value: string | number }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl bg-background p-4">
            <span className="text-xs text-ink-soft">{label}</span>
            <span className="text-2xl font-bold tracking-tight tabular-nums text-ink">{value}</span>
        </div>
    );
}

function meaning(e: FlakinessEntry) {
    if (e.is_quarantined) {
        return 'Quarantined: it is skipped when runs are dispatched, so it no longer blocks regressions. Release it once the cause is fixed so it protects you again.';
    }
    if (e.flake_score >= 0.66) {
        return 'This test passes and fails under the same conditions most of the time. Its result tells you little. Quarantine it so it stops blocking runs, then fix the timing or data it depends on.';
    }
    if (e.flake_score >= 0.33) {
        return 'This test sometimes fails for no reason the code explains. Keep an eye on it; quarantine it if it starts blocking releases.';
    }
    return 'Mostly stable. It has wobbled a little, but not enough to stop trusting it.';
}

// ---------------------------------------------------------------------------
// Detail — quarantine/release act on all FlakeRecords for the case
// ---------------------------------------------------------------------------

function FlakeDetail({ entry, projectId }: { entry: FlakinessEntry; projectId: number }) {
    const queryClient = useQueryClient();

    const mutation = useMutation({
        mutationFn: async (action: 'quarantine' | 'release') => {
            // The flakiness summary is keyed by test case, but quarantine/release
            // operate on individual FlakeRecord rows (a case may have a case-level
            // row plus per-step rows). Fetch them and toggle each.
            const records = await flakesApi.list(entry.test_case_id);
            if (records.length === 0) {
                throw new Error('No flake records found for this test case');
            }
            await Promise.all(
                records.map((r) =>
                    action === 'quarantine' ? flakesApi.quarantine(r.id) : flakesApi.release(r.id),
                ),
            );
            return action;
        },
        onSuccess: (action) => {
            queryClient.invalidateQueries({ queryKey: ['flakiness', projectId] });
            toast.success(action === 'quarantine'
                ? `"${entry.name}" quarantined — it will be skipped at dispatch`
                : `"${entry.name}" released back into runs`);
        },
        onError: (err: unknown) => toast.error(`Failed to update quarantine: ${errorDetail(err)}`),
    });

    return (
        <DetailCard label="Selected flaky test">
            <div className="flex flex-col gap-2">
                <Pill tone={entry.is_quarantined ? 'butter' : 'mint'} className="self-start">
                    {entry.is_quarantined ? 'Quarantined' : 'Still running'}
                </Pill>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{entry.name}</h2>
                <span className="text-sm text-ink-soft">Case #{entry.test_case_id}</span>
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow right={<span className="font-mono text-ink">{pctOf(entry.flake_score)}%</span>}>Flake score</Eyebrow>
                <FlakeScoreBar score={entry.flake_score} large />
                <span className="flex justify-between text-xs text-ink-soft"><span>0% stable</span><span>100% pure flake</span></span>
            </div>

            <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3">
                <Fact label="Runs sampled" value={entry.sample_count} />
                <Fact label="Recent failures" value={entry.recent_failures} />
            </div>

            <Panel title="What this means" callout>{meaning(entry)}</Panel>

            {entry.last_failure_message && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Last failure</Eyebrow>
                    <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-2xl border border-border bg-background p-4 font-mono text-xs leading-relaxed text-ink">{entry.last_failure_message}</pre>
                </div>
            )}

            <div className="flex flex-col gap-2 border-t border-border pt-5">
                <div className="flex flex-wrap gap-2.5">
                    {entry.is_quarantined ? (
                        <Button size="lg" disabled={mutation.isPending} onClick={() => mutation.mutate('release')}>
                            <ShieldCheck className="h-4 w-4" /> Release back into runs
                        </Button>
                    ) : (
                        <Button size="lg" disabled={mutation.isPending} onClick={() => mutation.mutate('quarantine')}>
                            <ShieldAlert className="h-4 w-4" /> Quarantine
                        </Button>
                    )}
                </div>
                <p className="text-xs text-ink-soft">This changes every flake record for the case, including per-step ones.</p>
            </div>
        </DetailCard>
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FlakyTests() {
    const projectId = useActiveProjectId();
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const { data: entries, isLoading, error } = useQuery({
        queryKey: ['flakiness', projectId],
        queryFn: () => flakesApi.projectFlakiness(projectId!),
        enabled: !!projectId,
    });

    const list = [...(entries ?? [])].sort((a, b) => b.flake_score - a.flake_score);
    const groups = [
        { name: 'Still in runs', items: list.filter((e) => !e.is_quarantined) },
        { name: 'Quarantined', items: list.filter((e) => e.is_quarantined) },
    ].filter((g) => g.items.length > 0);
    const selected = list.find((e) => e.test_case_id === selectedId) ?? groups[0]?.items[0] ?? null;
    const quarantined = list.filter((e) => e.is_quarantined).length;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Flaky tests"
                subtitle={
                    !projectId ? 'Pick a project in the sidebar.'
                        : entries ? `${list.length} test${list.length === 1 ? '' : 's'} pass and fail under the same conditions · ${quarantined} quarantined`
                            : 'Tests that pass and fail under the same conditions. Quarantine them so they stop blocking runs.'
                }
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to review its flaky tests.</EmptyState>
            ) : isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load flaky tests">{errorDetail(error)}</EmptyState>
            ) : list.length === 0 ? (
                <EmptyState title="No flaky tests found">
                    When a test's retries show it passing and failing under the same conditions, it appears here with a flake score so you can quarantine it.
                </EmptyState>
            ) : (
                <SplitView
                    list={groups.map((g) => (
                        <ListGroup key={g.name} title={g.name} count={g.items.length}>
                            {g.items.map((e) => (
                                <ListRow
                                    key={e.test_case_id}
                                    selected={selected?.test_case_id === e.test_case_id}
                                    onSelect={() => setSelectedId(e.test_case_id)}
                                    leading={<KindChip kind={e.is_quarantined ? 'skip' : 'flaky'} size="sm" />}
                                    title={<span className="line-clamp-2 break-words">{e.name}</span>}
                                    meta={
                                        <span className="flex flex-col gap-1.5">
                                            <span>{e.recent_failures} recent failure{e.recent_failures === 1 ? '' : 's'} · {e.sample_count} runs sampled</span>
                                            <span className="block max-w-[160px]"><FlakeScoreBar score={e.flake_score} /></span>
                                        </span>
                                    }
                                    trailing={<span className="font-mono text-[13px] tabular-nums text-ink">{pctOf(e.flake_score)}%</span>}
                                />
                            ))}
                        </ListGroup>
                    ))}
                    detail={selected && <FlakeDetail key={selected.test_case_id} entry={selected} projectId={projectId} />}
                />
            )}
        </div>
    );
}
