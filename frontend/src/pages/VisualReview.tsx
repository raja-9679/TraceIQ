import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, ExternalLink, History, ImageOff, RefreshCw, ShieldCheck, Trash2, Undo2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';
import {
    visualBaselinesApi, findLatestComparison, resolveImageUrl,
    VisualBaseline, VisualComparisonArtifacts, BaselineHistoryEntry,
} from '@/api/visual';
import { getTestCaseInfo, TestCaseInfo } from '@/api/proposals';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    DetailCard, EmptyState, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';

const formatDate = (dateString?: string | null) => {
    if (!dateString) return '—';
    return new Date(dateString).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
};

const targetLabel = (b: VisualBaseline) => (b.device ? `${b.browser} · ${b.device}` : b.browser);

// ---------------------------------------------------------------------------
// Comparison panel — baseline | actual | diff, resolved to presigned URLs
// ---------------------------------------------------------------------------

interface ComparisonData {
    artifacts: VisualComparisonArtifacts | null;
    baselineUrl: string | null;
    candidateUrl: string | null;
    diffUrl: string | null;
}

function ImagePane({ label, hint, url }: { label: string; hint: string; url: string | null }) {
    return (
        <figure className="flex min-w-0 flex-col gap-2">
            <figcaption className="flex flex-col">
                <span className="text-sm font-semibold text-ink">{label}</span>
                <span className="text-xs text-ink-soft">{hint}</span>
            </figcaption>
            {url ? (
                <a href={url} target="_blank" rel="noreferrer" className="group relative block rounded-2xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Open ${label.toLowerCase()} image in a new tab`}>
                    <img
                        src={url}
                        alt={label}
                        className="max-h-[420px] w-full rounded-2xl border border-border bg-white object-contain"
                    />
                    <span className="absolute right-2 top-2 rounded-full bg-white/95 p-1.5 opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" aria-hidden="true">
                        <ExternalLink className="h-3.5 w-3.5 text-ink" />
                    </span>
                </a>
            ) : (
                <div className="flex h-40 w-full flex-col items-center justify-center gap-1.5 rounded-2xl border border-dashed border-border bg-background text-ink-soft">
                    <ImageOff className="h-5 w-5" aria-hidden="true" />
                    <span className="text-xs">Not available</span>
                </div>
            )}
        </figure>
    );
}

function ComparisonPanel({ baseline, caseName }: { baseline: VisualBaseline; caseName: string }) {
    const queryClient = useQueryClient();
    const [kept, setKept] = useState(false);

    const { data, isLoading, error } = useQuery<ComparisonData>({
        queryKey: ['visual-comparison', baseline.id],
        queryFn: async () => {
            const [artifacts, baselineUrl] = await Promise.all([
                findLatestComparison(baseline.test_case_id, baseline.step_id).catch(() => null),
                resolveImageUrl(baseline.image_url).catch(() => null),
            ]);
            const [candidateUrl, diffUrl] = await Promise.all([
                artifacts?.candidateKey ? resolveImageUrl(artifacts.candidateKey).catch(() => null) : Promise.resolve(null),
                artifacts?.diffKey ? resolveImageUrl(artifacts.diffKey).catch(() => null) : Promise.resolve(null),
            ]);
            return { artifacts, baselineUrl, candidateUrl, diffUrl };
        },
        staleTime: 30_000,
    });

    const acceptMutation = useMutation({
        mutationFn: async () => {
            if (!data?.artifacts?.runId) throw new Error('No candidate screenshot available');
            // Durable promotion: the backend copies the run's candidate screenshot
            // into a stable baseline object (survives artifact cleanup / URL expiry)
            // and upserts the baseline row for this case/step/browser/device.
            await visualBaselinesApi.promote({
                test_case_id: baseline.test_case_id,
                step_id: baseline.step_id,
                run_id: data.artifacts.runId,
                browser: baseline.browser,
                device: baseline.device,
                tolerance: baseline.tolerance,
            });
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
            queryClient.invalidateQueries({ queryKey: ['visual-baseline-history', baseline.id] });
            toast.success('New baseline accepted', {
                description: `The latest screenshot is now the pinned baseline for "${caseName}".`,
            });
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
            toast.error(`Failed to accept baseline: ${err.response?.data?.detail || err.message}`),
    });

    // "Keep" is a review decision too: it is recorded (who, which run) so a
    // rejected visual change is as traceable as an accepted one.
    const keepMutation = useMutation({
        mutationFn: () => visualBaselinesApi.keep(baseline.id, data?.artifacts?.runId ?? null),
        onSuccess: () => {
            setKept(true);
            queryClient.invalidateQueries({ queryKey: ['visual-baseline-history', baseline.id] });
            toast.info('Baseline kept', { description: 'The existing baseline remains pinned. Your decision was recorded.' });
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
            toast.error(`Failed to record decision: ${err.response?.data?.detail || err.message}`),
    });

    if (isLoading) {
        return (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4" aria-busy="true" aria-label="Loading comparison images">
                {[0, 1, 2].map((i) => <div key={i} className="h-40 animate-pulse rounded-2xl bg-background" />)}
            </div>
        );
    }
    if (error || !data) {
        return <p className="text-sm text-pastel-ink-peach">Failed to load comparison for this baseline.</p>;
    }

    const { artifacts } = data;

    return (
        <div className="flex flex-col gap-5">
            {artifacts ? (
                <div className="flex flex-wrap items-center gap-2 text-sm text-ink-soft">
                    <span>
                        Latest capture from <Link to={`/runs/${artifacts.runId}`} className="font-medium text-ink underline-offset-2 hover:underline">run #{artifacts.runId}</Link> ({formatDate(artifacts.runCreatedAt)})
                    </span>
                    <Pill tone={artifacts.runStatus === 'passed' ? 'mint' : 'peach'}>{artifacts.runStatus}</Pill>
                </div>
            ) : (
                <p className="text-sm text-ink-soft">
                    No recent run captured a screenshot for this step, so only the pinned baseline is shown.
                </p>
            )}

            <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
                <ImagePane label="Baseline" hint="What it should look like" url={data.baselineUrl} />
                <ImagePane label="Actual" hint="What the latest run saw" url={data.candidateUrl} />
                <ImagePane label="Difference" hint="Changed pixels highlighted" url={data.diffUrl} />
            </div>

            <Panel title="What to do" callout>
                If the change is intentional, accept the latest screenshot as the new baseline. If it is a regression, keep the current baseline and fix the page. Your decision is recorded either way.
            </Panel>

            <div className="flex flex-wrap items-center gap-2.5">
                <Button
                    size="lg"
                    disabled={!data.artifacts?.candidateKey || acceptMutation.isPending}
                    onClick={() => acceptMutation.mutate()}
                    title={data.artifacts?.candidateKey ? 'Pin the latest screenshot as the new baseline' : 'No candidate screenshot available from recent runs'}
                >
                    {acceptMutation.isPending ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                    Accept new baseline
                </Button>
                <Button
                    size="lg"
                    variant="outline"
                    disabled={kept || acceptMutation.isPending || keepMutation.isPending}
                    onClick={() => keepMutation.mutate()}
                >
                    <ShieldCheck className="h-4 w-4" /> {kept ? 'Baseline kept' : 'Keep baseline'}
                </Button>
            </div>

            <BaselineHistory baseline={baseline} />
        </div>
    );
}

const HISTORY_LABEL: Record<string, string> = {
    accept: 'Accepted new baseline',
    keep: 'Kept baseline',
    rollback: 'Rolled back',
};

function BaselineHistory({ baseline }: { baseline: VisualBaseline }) {
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(false);
    const { data: entries = [], isLoading } = useQuery<BaselineHistoryEntry[]>({
        queryKey: ['visual-baseline-history', baseline.id],
        queryFn: () => visualBaselinesApi.history(baseline.id),
        enabled: open,
    });
    const rollbackMutation = useMutation({
        mutationFn: (image: string) => visualBaselinesApi.rollback(baseline.id, image),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
            queryClient.invalidateQueries({ queryKey: ['visual-comparison', baseline.id] });
            queryClient.invalidateQueries({ queryKey: ['visual-baseline-history', baseline.id] });
            toast.success('Baseline rolled back');
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
            toast.error(`Rollback failed: ${err.response?.data?.detail || err.message}`),
    });

    return (
        <div className="flex flex-col gap-3 border-t border-border pt-4">
            <Button
                type="button"
                variant="ghost"
                className="self-start px-3"
                onClick={() => setOpen(o => !o)}
                aria-expanded={open}
            >
                <History className="h-4 w-4" /> {open ? 'Hide decision history' : 'Decision history'}
            </Button>
            {open && (
                <div className="flex flex-col gap-2">
                    {isLoading && <div className="h-12 animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading history" />}
                    {!isLoading && entries.length === 0 && (
                        <p className="text-sm text-ink-soft">No recorded decisions yet. Decisions made before this change were not recorded.</p>
                    )}
                    {entries.length > 0 && (
                        <ul className="overflow-hidden rounded-2xl border border-border">
                            {entries.map((e, i) => (
                                <li key={i} className={cn('flex min-h-12 flex-wrap items-center gap-x-2 gap-y-1 px-4 py-2.5 text-sm', i > 0 && 'border-t border-[#F0EEE8]')}>
                                    <span className="font-medium text-ink">{HISTORY_LABEL[e.action] || e.action}</span>
                                    <span className="text-ink-soft">{formatDate(e.at)}</span>
                                    {e.actor_label && <span className="text-ink-soft">by {e.actor_label}</span>}
                                    {e.run_id && <span className="text-ink-soft">· run #{e.run_id}</span>}
                                    {e.is_current && e.action !== 'keep' && <Pill tone="mint">Current</Pill>}
                                    {e.action !== 'keep' && e.previous_image && (
                                        <Button
                                            size="sm"
                                            variant="outline"
                                            disabled={rollbackMutation.isPending}
                                            onClick={() => rollbackMutation.mutate(e.previous_image!)}
                                            className="ml-auto h-9 px-3"
                                            title="Re-pin the baseline that this change replaced"
                                        >
                                            <Undo2 className="h-3.5 w-3.5" /> Restore previous
                                        </Button>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
}

// ---------------------------------------------------------------------------
// Baseline detail
// ---------------------------------------------------------------------------

function BaselineDetail({ baseline, caseInfo }: { baseline: VisualBaseline; caseInfo?: TestCaseInfo }) {
    const queryClient = useQueryClient();
    const caseName = caseInfo?.name || `Case #${baseline.test_case_id}`;

    const deleteMutation = useMutation({
        mutationFn: () => visualBaselinesApi.delete(baseline.id),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
            toast.success('Baseline deleted', {
                description: `The pinned baseline for "${caseName}" (step ${baseline.step_id}) was removed.`,
            });
        },
        onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
            toast.error(`Failed to delete baseline: ${err.response?.data?.detail || err.message}`),
    });

    return (
        <DetailCard label="Selected visual baseline">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-2">
                    <Pill tone="neutral">{baseline.browser}</Pill>
                    {baseline.device && <Pill tone="neutral">{baseline.device}</Pill>}
                    <Pill tone="lavender">Tolerance {(baseline.tolerance * 100).toFixed(1)}%</Pill>
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{caseName}</h2>
                <span className="text-sm text-ink-soft">
                    Step <span className="font-mono">{baseline.step_id}</span> · baseline pinned {formatDate(baseline.created_at)}
                </span>
            </div>

            <ComparisonPanel baseline={baseline} caseName={caseName} />

            <div className="flex flex-col gap-2 border-t border-border pt-4">
                <Eyebrow>Remove</Eyebrow>
                <div className="flex flex-wrap items-center gap-3">
                    <Button
                        variant="outline"
                        disabled={deleteMutation.isPending}
                        onClick={() => {
                            if (window.confirm(`Delete the baseline for "${caseName}" (step ${baseline.step_id})? Subsequent runs will have nothing to compare against.`)) {
                                deleteMutation.mutate();
                            }
                        }}
                    >
                        <Trash2 className="h-4 w-4" /> Delete baseline
                    </Button>
                    <span className="text-xs text-ink-soft">Later runs will have nothing to compare this step against.</span>
                </div>
            </div>
        </DetailCard>
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function VisualReview() {
    const projectId = useActiveProjectId();
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const { data: baselines, isLoading, error } = useQuery({
        queryKey: ['visual-baselines', projectId],
        queryFn: () => visualBaselinesApi.list(projectId ? { projectId } : undefined),
    });

    // Resolve case info for each unique baseline case so we can show names and
    // filter to the active project (baselines carry no project_id themselves).
    const caseIds = useMemo(
        () => Array.from(new Set((baselines || []).map((b) => b.test_case_id))),
        [baselines],
    );
    const caseQueries = useQueries({
        queries: caseIds.map((id) => ({
            queryKey: ['case', id],
            queryFn: () => getTestCaseInfo(id),
            staleTime: 60_000,
            retry: false,
        })),
    });
    const casesById = useMemo(() => {
        const map = new Map<number, TestCaseInfo>();
        caseQueries.forEach((q, i) => {
            if (q.data) map.set(caseIds[i], q.data);
        });
        return map;
    }, [caseQueries, caseIds]);
    const casesResolving = caseQueries.some((q) => q.isLoading);

    const visibleBaselines = useMemo(() => {
        if (!baselines) return [];
        if (!projectId) return baselines;
        return baselines.filter((b) => {
            const info = casesById.get(b.test_case_id);
            // Keep rows whose case is still resolving (or inaccessible) out of view
            // only when we positively know they belong to another project.
            if (!info) return casesResolving;
            return info.project_id === projectId;
        });
    }, [baselines, projectId, casesById, casesResolving]);

    const caseNameOf = (b: VisualBaseline) => casesById.get(b.test_case_id)?.name || `Case #${b.test_case_id}`;
    const byTarget = new Map<string, VisualBaseline[]>();
    for (const b of visibleBaselines) byTarget.set(targetLabel(b), [...(byTarget.get(targetLabel(b)) ?? []), b]);
    const groups = [...byTarget.entries()].map(([name, items]) => ({ name, items }));
    const selected = visibleBaselines.find((b) => b.id === selectedId) ?? groups[0]?.items[0] ?? null;
    const loading = isLoading || (baselines && baselines.length > 0 && casesResolving && visibleBaselines.length === 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Visual review"
                subtitle={
                    !loading && !error && visibleBaselines.length > 0
                        ? `${visibleBaselines.length} pinned screenshot${visibleBaselines.length === 1 ? '' : 's'}. Compare each with the latest run, then accept the change or keep the baseline.`
                        : 'Compare pinned screenshots with the latest run, then accept intentional changes or keep the baseline.'
                }
            />

            {loading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load visual baselines">{(error as Error).message}</EmptyState>
            ) : !baselines || baselines.length === 0 || visibleBaselines.length === 0 ? (
                <EmptyState title={`No visual baselines${projectId ? ' in this project' : ''}`}>
                    Add an <code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs text-ink">expect-visual-match</code> step to a test and pin a baseline screenshot. Comparisons from later runs will appear here for review.
                </EmptyState>
            ) : (
                <SplitView
                    list={groups.map((g) => (
                        <ListGroup key={g.name} title={g.name} count={g.items.length}>
                            {g.items.map((b) => (
                                <ListRow
                                    key={b.id}
                                    selected={selected?.id === b.id}
                                    onSelect={() => setSelectedId(b.id)}
                                    leading={<KindChip kind="visual" size="sm" />}
                                    title={<span className="line-clamp-2 break-words">{caseNameOf(b)}</span>}
                                    meta={`Step ${b.step_id} · pinned ${formatDate(b.created_at)}`}
                                    trailing={<span className="font-mono text-xs tabular-nums text-ink-soft" title="Tolerance">{(b.tolerance * 100).toFixed(1)}%</span>}
                                />
                            ))}
                        </ListGroup>
                    ))}
                    detail={selected && <BaselineDetail key={selected.id} baseline={selected} caseInfo={casesById.get(selected.test_case_id)} />}
                />
            )}
        </div>
    );
}
