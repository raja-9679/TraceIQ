import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Layers, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    DetailCard, EmptyState, Eyebrow, KindChip, ListCard, ListRow, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import {
    visualBaselinesApi, visualReviewApi, type VisualReviewItem, type VisualReviewRun, type VisualReviewRunSummary,
} from '@/api/visual';
import { CommentThread } from './CommentThread';
import { ImagePane } from './ImagePane';
import {
    arrangeReview, decisionLabel, formatRatio, itemTitle, kindLabel, regionLabel, statusLabel,
    type GroupView, type ReviewFilter, type ReviewSort,
} from './reviewHelpers';

type ApiError = Error & { response?: { data?: { detail?: string } } };
const errText = (err: ApiError) => err.response?.data?.detail || err.message;

const formatDate = (iso?: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';

type Selection = { kind: 'item'; id: number } | { kind: 'group'; key: string } | null;

function ControlSelect<T extends string>({ id, label, value, onChange, options }: {
    id: string; label: string; value: T; onChange: (v: T) => void; options: { value: T; label: string }[];
}) {
    return (
        <div className="flex min-w-[160px] flex-col gap-1">
            <label htmlFor={id} className="text-xs font-semibold text-ink-soft">{label}</label>
            <Select value={value} onValueChange={(v) => onChange(v as T)}>
                <SelectTrigger id={id} className="h-10 rounded-full bg-white"><SelectValue /></SelectTrigger>
                <SelectContent>
                    {options.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                </SelectContent>
            </Select>
        </div>
    );
}

function ItemPills({ item }: { item: VisualReviewItem }) {
    return (
        <div className="flex flex-wrap gap-2">
            <Pill tone={item.kind === 'element' ? 'lavender' : 'neutral'}>{kindLabel(item)}</Pill>
            <Pill tone="neutral">{item.device ? `${item.browser} · ${item.device}` : item.browser}</Pill>
            <Pill tone={item.status === 'failed' ? 'peach' : item.status === 'passed' ? 'mint' : 'butter'}>{statusLabel(item.status)}</Pill>
            {item.diff_ratio !== null && <Pill tone="pink">Changed {formatRatio(item.diff_ratio)}</Pill>}
            {item.size_mismatch && <Pill tone="butter">Size changed</Pill>}
            {decisionLabel(item.decision) && <Pill tone="mint">{decisionLabel(item.decision)}</Pill>}
        </div>
    );
}

function ItemRow({ item, selected, onSelect }: { item: VisualReviewItem; selected: boolean; onSelect: () => void }) {
    return (
        <ListRow
            selected={selected}
            onSelect={onSelect}
            leading={<KindChip kind="visual" size="sm" />}
            title={<span className="line-clamp-2 break-words">{itemTitle(item)}</span>}
            meta={`${kindLabel(item)} · step ${item.step_id}${item.comment_count ? ` · ${item.comment_count} comment${item.comment_count === 1 ? '' : 's'}` : ''}${item.decision ? ` · ${decisionLabel(item.decision)}` : ''}`}
            trailing={<span className="font-mono text-xs tabular-nums text-ink-soft" title="Changed pixels">{formatRatio(item.diff_ratio)}</span>}
        />
    );
}

function ItemDetail({ projectId, runId, item, canEdit, onOpenGroup, onChanged }: {
    projectId: number; runId: number; item: VisualReviewItem; canEdit: boolean;
    onOpenGroup?: () => void; onChanged: () => void;
}) {
    const accept = useMutation({
        mutationFn: () => visualBaselinesApi.promote({
            test_case_id: item.test_case_id!, step_id: item.step_id, run_id: runId,
            browser: item.browser, device: item.device, element_key: item.element_key,
            comparison_id: item.id, ...(item.tolerance !== null ? { tolerance: item.tolerance } : {}),
        }),
        onSuccess: () => { toast.success('New baseline accepted'); onChanged(); },
        onError: (e: ApiError) => toast.error(`Failed to accept: ${errText(e)}`),
    });
    const keep = useMutation({
        mutationFn: () => visualBaselinesApi.keep(item.baseline_id!, runId),
        onSuccess: () => { toast.info('Baseline kept', { description: 'Your decision was recorded.' }); onChanged(); },
        onError: (e: ApiError) => toast.error(`Failed to record decision: ${errText(e)}`),
    });

    return (
        <DetailCard label="Selected visual change">
            <div className="flex flex-col gap-2">
                <ItemPills item={item} />
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{itemTitle(item)}</h2>
                <span className="text-sm text-ink-soft">
                    Step <span className="font-mono">{item.step_id}</span>
                    {item.selector && <> · element <span className="font-mono">{item.selector}</span></>}
                    {item.diff_pixels !== null && item.total_pixels ? ` · ${item.diff_pixels.toLocaleString()} of ${item.total_pixels.toLocaleString()} pixels changed` : ''}
                </span>
                {item.group_key && onOpenGroup && (
                    <Button variant="outline" size="sm" className="self-start" onClick={onOpenGroup}>
                        <Layers className="h-4 w-4" /> Open the group with the same change
                    </Button>
                )}
            </div>

            <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4">
                <ImagePane label="Baseline" hint="What it should look like" url={item.baseline_url} />
                <ImagePane label="Actual" hint={`What run #${runId} saw`} url={item.candidate_url} />
                <ImagePane label="Difference" hint="Changed pixels highlighted" url={item.diff_url} />
            </div>

            {item.regions.length > 0 && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Changed areas</Eyebrow>
                    <ul className="flex flex-wrap gap-2 text-xs text-ink-soft">
                        {item.regions.slice(0, 6).map((r, i) => (
                            <li key={i} className="rounded-full border border-border px-3 py-1 font-mono">{regionLabel(r)}</li>
                        ))}
                        {item.regions.length > 6 && <li className="px-1 py-1">+{item.regions.length - 6} more</li>}
                    </ul>
                </div>
            )}

            <Panel title="What to do" callout>
                If the change is intended, accept this screenshot as the new baseline. If it is a regression, keep the current baseline and fix the page. Either way the decision is recorded.
            </Panel>

            {canEdit ? (
                <div className="flex flex-wrap items-center gap-2.5">
                    <Button size="lg" disabled={!item.has_candidate || !item.test_case_id || accept.isPending} onClick={() => accept.mutate()}>
                        <Check className="h-4 w-4" /> {item.status === 'capture_only' ? 'Pin as first baseline' : 'Accept new baseline'}
                    </Button>
                    <Button size="lg" variant="outline" disabled={!item.baseline_id || keep.isPending} onClick={() => keep.mutate()}>
                        <ShieldCheck className="h-4 w-4" /> Keep baseline
                    </Button>
                </div>
            ) : (
                <p className="text-sm text-ink-soft">Accepting or keeping a baseline needs the editor role on this project.</p>
            )}

            <div className="border-t border-border pt-4">
                <CommentThread projectId={projectId} target={{ comparisonId: item.id }} canEdit={canEdit}
                    title="Comments on this change" onChanged={onChanged} />
            </div>
        </DetailCard>
    );
}

function GroupDetail({ projectId, runId, view, canEdit, onSelectItem, onChanged }: {
    projectId: number; runId: number; view: GroupView; canEdit: boolean;
    onSelectItem: (id: number) => void; onChanged: () => void;
}) {
    const { group, items } = view;
    const accept = useMutation({
        mutationFn: () => visualReviewApi.acceptGroup(projectId, group.key, runId),
        onSuccess: (r) => {
            toast.success(`Accepted ${r.accepted.length} baseline${r.accepted.length === 1 ? '' : 's'}`,
                r.skipped.length ? { description: `${r.skipped.length} skipped: ${r.skipped.map((s) => s.reason).join('; ')}` } : undefined);
            onChanged();
        },
        onError: (e: ApiError) => toast.error(`Failed to accept the group: ${errText(e)}`),
    });
    const keep = useMutation({
        mutationFn: () => visualReviewApi.keepGroup(projectId, group.key, runId),
        onSuccess: (r) => {
            toast.info(`Kept ${r.kept.length} baseline${r.kept.length === 1 ? '' : 's'}`,
                r.skipped.length ? { description: `${r.skipped.length} had no baseline to keep.` } : undefined);
            onChanged();
        },
        onError: (e: ApiError) => toast.error(`Failed to record the decision: ${errText(e)}`),
    });

    return (
        <DetailCard label="Selected group of related changes">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-2">
                    <Pill tone="pink">Same change in {group.case_count} case{group.case_count === 1 ? '' : 's'}</Pill>
                    <Pill tone="neutral">Up to {formatRatio(group.max_diff_ratio)} changed</Pill>
                </div>
                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{group.size} related changes</h2>
                <span className="text-sm text-ink-soft">
                    Grouped because the changed area looks the same before and after ({regionLabel(group.region)} in the first one). Check one or two, then decide for all of them.
                </span>
            </div>

            <ul className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-3">
                {items.map((i) => (
                    <li key={i.id}>
                        <button type="button" onClick={() => onSelectItem(i.id)}
                            className="flex w-full flex-col gap-2 rounded-2xl border border-border bg-white p-2 text-left hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                            {i.diff_url
                                ? <img src={i.diff_url} alt={`Difference for ${itemTitle(i)}`} className="h-28 w-full rounded-xl bg-background object-contain" />
                                : <span className="flex h-28 items-center justify-center rounded-xl bg-background text-xs text-ink-soft">No diff image</span>}
                            <span className="line-clamp-2 text-sm font-medium text-ink">{itemTitle(i)}</span>
                            <span className="text-xs text-ink-soft">{kindLabel(i)} · {formatRatio(i.diff_ratio)}{i.decision ? ` · ${decisionLabel(i.decision)}` : ''}</span>
                        </button>
                    </li>
                ))}
            </ul>

            {canEdit ? (
                <div className="flex flex-wrap items-center gap-2.5">
                    <Button size="lg" disabled={accept.isPending || keep.isPending}
                        onClick={() => { if (window.confirm(`Accept the new screenshot as the baseline for all ${group.size} items in this group?`)) accept.mutate(); }}>
                        <Check className="h-4 w-4" /> Accept all in group
                    </Button>
                    <Button size="lg" variant="outline" disabled={accept.isPending || keep.isPending} onClick={() => keep.mutate()}>
                        <ShieldCheck className="h-4 w-4" /> Keep all in group
                    </Button>
                    <span className="text-xs text-ink-soft">Each baseline still gets its own entry in the decision history.</span>
                </div>
            ) : (
                <p className="text-sm text-ink-soft">Deciding for a group needs the editor role on this project.</p>
            )}

            <div className="border-t border-border pt-4">
                <CommentThread projectId={projectId} target={{ runId, groupKey: group.key }} canEdit={canEdit}
                    title="Comments on this group" onChanged={onChanged} />
            </div>
        </DetailCard>
    );
}

/** One run's visual comparisons, related changes grouped (WP14e). */
export function RunReview({ projectId, canEdit }: { projectId: number; canEdit: boolean }) {
    const queryClient = useQueryClient();
    const [runId, setRunId] = useState<number | null>(null);
    const [filter, setFilter] = useState<ReviewFilter>('changed');
    const [sort, setSort] = useState<ReviewSort>('diff');
    const [selection, setSelection] = useState<Selection>(null);

    const runs = useQuery<VisualReviewRunSummary[]>({
        queryKey: ['visual-review-runs', projectId],
        queryFn: () => visualReviewApi.runs(projectId),
    });
    const review = useQuery<VisualReviewRun>({
        queryKey: ['visual-review', projectId, runId],
        queryFn: () => visualReviewApi.review(projectId, runId),
    });
    const refresh = () => {
        queryClient.invalidateQueries({ queryKey: ['visual-review', projectId] });
        queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
    };

    const data = review.data;
    const arranged = useMemo(
        () => arrangeReview(data?.items ?? [], data?.groups ?? [], filter, sort),
        [data, filter, sort],
    );
    const shownRunId = data?.run?.id ?? null;
    const firstSelection: Selection = arranged.groups[0]
        ? { kind: 'group', key: arranged.groups[0].group.key }
        : arranged.singles[0] ? { kind: 'item', id: arranged.singles[0].id } : null;
    const current = selection ?? firstSelection;
    const selectedGroup = current?.kind === 'group' ? arranged.groups.find((g) => g.group.key === current.key) : undefined;
    const selectedItem = current?.kind === 'item' ? data?.items.find((i) => i.id === current.id) : undefined;
    const groupOf = (key: string | null) => (key ? arranged.groups.find((g) => g.group.key === key) : undefined);

    const runOptions = (runs.data ?? []).map((r) => ({
        value: String(r.run_id),
        label: `Run #${r.run_id} · ${formatDate(r.created_at)} · ${r.changed} changed`,
    }));

    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-end gap-3 rounded-3xl border border-border bg-white px-5 py-4">
                <ControlSelect id="vr-run" label="Run" value={String(runId ?? shownRunId ?? '')}
                    onChange={(v) => { setRunId(Number(v)); setSelection(null); }}
                    options={runOptions.length ? runOptions : [{ value: String(shownRunId ?? ''), label: shownRunId ? `Run #${shownRunId}` : 'No runs yet' }]} />
                <ControlSelect id="vr-filter" label="Show" value={filter} onChange={(v) => { setFilter(v); setSelection(null); }}
                    options={[{ value: 'changed', label: 'Changes and new captures' }, { value: 'undecided', label: 'Not yet decided' }, { value: 'all', label: 'Everything compared' }]} />
                <ControlSelect id="vr-sort" label="Sort by" value={sort} onChange={setSort}
                    options={[{ value: 'diff', label: 'Largest change first' }, { value: 'case', label: 'Case name' }]} />
                {data?.run && (
                    <span className="ml-auto text-sm text-ink-soft">
                        <Link to={`/runs/${data.run.id}`} className="font-medium text-ink underline-offset-2 hover:underline">Run #{data.run.id}</Link>
                        {' '}· {formatDate(data.run.created_at)}{data.run.git_branch ? ` · ${data.run.git_branch}` : ''}
                    </span>
                )}
            </div>

            {review.isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : review.error ? (
                <EmptyState title="Couldn't load the visual review">{(review.error as Error).message}</EmptyState>
            ) : !data?.run ? (
                <EmptyState title="No visual comparisons yet">
                    Runs with an <code className="rounded bg-secondary px-1.5 py-0.5 font-mono text-xs text-ink">expect-visual-match</code> step record each comparison here, grouped by likely same change.
                </EmptyState>
            ) : arranged.groups.length === 0 && arranged.singles.length === 0 ? (
                <EmptyState title="Nothing to review in this run">
                    Every comparison was within tolerance. Choose "Everything compared" to see them all.
                </EmptyState>
            ) : (
                <SplitView
                    list={(
                        <>
                            {arranged.groups.map((g) => (
                                <ListCard key={g.group.key}
                                    title={`Same change in ${g.group.case_count} case${g.group.case_count === 1 ? '' : 's'}`}
                                    right={(
                                        <Button size="sm" variant={current?.kind === 'group' && current.key === g.group.key ? 'default' : 'outline'}
                                            className="h-8 px-3" onClick={() => setSelection({ kind: 'group', key: g.group.key })}>
                                            <Layers className="h-3.5 w-3.5" /> Review group{g.group.comment_count ? ` · ${g.group.comment_count}` : ''}
                                        </Button>
                                    )}>
                                    {g.items.map((i) => (
                                        <ItemRow key={i.id} item={i} selected={current?.kind === 'item' && current.id === i.id}
                                            onSelect={() => setSelection({ kind: 'item', id: i.id })} />
                                    ))}
                                </ListCard>
                            ))}
                            {arranged.singles.length > 0 && (
                                <ListCard title={arranged.groups.length ? 'Other changes' : 'Changes'} count={arranged.singles.length}>
                                    {arranged.singles.map((i) => (
                                        <ItemRow key={i.id} item={i} selected={current?.kind === 'item' && current.id === i.id}
                                            onSelect={() => setSelection({ kind: 'item', id: i.id })} />
                                    ))}
                                </ListCard>
                            )}
                        </>
                    )}
                    detail={shownRunId && (selectedGroup ? (
                        <GroupDetail key={selectedGroup.group.key} projectId={projectId} runId={shownRunId} view={selectedGroup}
                            canEdit={canEdit} onSelectItem={(id) => setSelection({ kind: 'item', id })} onChanged={refresh} />
                    ) : selectedItem ? (
                        <ItemDetail key={selectedItem.id} projectId={projectId} runId={shownRunId} item={selectedItem} canEdit={canEdit}
                            onOpenGroup={groupOf(selectedItem.group_key)
                                ? () => setSelection({ kind: 'group', key: selectedItem.group_key! }) : undefined}
                            onChanged={refresh} />
                    ) : null)}
                />
            )}
        </div>
    );
}
