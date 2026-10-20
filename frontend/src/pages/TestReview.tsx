import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { useActiveProjectId } from '@/hooks/useActiveProject';
import { getTestReview, type ReviewOwnerFilter } from '@/api/caseOwnership';
import { errorDetail } from '@/components/flakes/flakeHelpers';
import { CaseOwnershipPanel } from '@/components/ownership/CaseOwnershipPanel';
import { groupForReview, personLabel, REVIEW_LABEL, reviewSummary } from '@/components/ownership/ownershipHelpers';
import {
    DetailCard, EmptyState, KindChip, ListGroup, ListRow, PageHeader, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';

// "Test review" (WP16b, V5): the project's tests that need a person to look
// at them — never reviewed, overdue for their module's review interval, or
// owned by someone who no longer has access to the project.

type Tab = 'needs_review' | 'mine' | 'all';

export default function TestReview() {
    const projectId = useActiveProjectId();
    const [tab, setTab] = useState<Tab>('needs_review');
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const status = tab === 'all' ? 'all' : 'needs_review';
    const owner: ReviewOwnerFilter = tab === 'mine' ? 'me' : undefined;

    const { data, isLoading, error } = useQuery({
        queryKey: ['test-review', projectId, status, owner],
        queryFn: () => getTestReview(projectId!, status, owner),
        enabled: !!projectId,
    });

    const items = data?.items ?? [];
    const groups = groupForReview(items);
    const selected = items.find((c) => c.case_id === selectedId) ?? groups[0]?.items[0] ?? null;
    const counts = data?.counts;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Test review"
                subtitle={
                    !projectId ? 'Pick a project in the sidebar.'
                        : counts ? `${counts.overdue} overdue · ${counts.never} never reviewed · ${counts.owner_no_access} with an owner who lost access · ${counts.unowned} unowned`
                            : 'Tests whose periodic review is due, and tests whose owner has moved on.'
                }
                toolbar={projectId ? (
                    <PanelTabs<Tab> label="Which tests" value={tab} onChange={(t) => { setTab(t); setSelectedId(null); }}
                        tabs={[
                            { id: 'needs_review', label: 'Needs review' },
                            { id: 'mine', label: 'Mine to review' },
                            { id: 'all', label: 'All tests' },
                        ]} />
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its tests' review status.</EmptyState>
            ) : isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load the review list">{errorDetail(error)}</EmptyState>
            ) : items.length === 0 ? (
                <EmptyState title={tab === 'all' ? 'No tests yet' : 'Nothing to review'}>
                    {tab === 'all' ? 'Tests in this project appear here with their owner and review status.'
                        : 'Every test is within its review interval. Intervals are set per module in the suite settings (default 90 days).'}
                </EmptyState>
            ) : (
                <SplitView
                    list={<>
                        {groups.map((g) => (
                            <ListGroup key={g.name} title={g.name} count={g.items.length}>
                                {g.items.map((c) => (
                                    <ListRow
                                        key={c.case_id}
                                        selected={selected?.case_id === c.case_id}
                                        onSelect={() => setSelectedId(c.case_id)}
                                        leading={<KindChip kind={c.review_status === 'ok' || c.review_status === 'not_required' ? 'pass' : 'review'} size="sm" />}
                                        title={<span className="line-clamp-2 break-words">{c.name}</span>}
                                        meta={<span className="flex flex-col gap-0.5">
                                            <span>{c.suite_name ? `${c.suite_name} · ` : ''}{personLabel(c.owner)}</span>
                                            <span>{reviewSummary(c)}</span>
                                        </span>}
                                    />
                                ))}
                            </ListGroup>
                        ))}
                        {data?.truncated && <p className="px-2 text-xs text-ink-soft">Showing the first {items.length} of {data.total}.</p>}
                    </>}
                    detail={selected && (
                        <DetailCard key={selected.case_id} label="Selected test">
                            <div className="flex flex-col gap-2">
                                <Pill tone={REVIEW_LABEL[selected.review_status].tone} className="self-start">
                                    {REVIEW_LABEL[selected.review_status].label}
                                </Pill>
                                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                                <span className="text-sm text-ink-soft">
                                    {selected.suite_name ? `${selected.suite_name} · ` : ''}Case #{selected.case_id}
                                    {selected.test_suite_id && (
                                        <> · <Link className="underline underline-offset-2" to={`/suites/${selected.test_suite_id}/cases/${selected.case_id}/edit`}>Open the test</Link></>
                                    )}
                                </span>
                            </div>
                            <CaseOwnershipPanel caseId={selected.case_id} projectId={projectId} />
                        </DetailCard>
                    )}
                />
            )}
        </div>
    );
}
