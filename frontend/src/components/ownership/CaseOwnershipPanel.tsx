import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2 } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Eyebrow, Pill } from '@/components/redesign/primitives';
import { getCaseOwnership, markCaseReviewed, setCaseOwnership, type CaseOwnership } from '@/api/caseOwnership';
import { errorDetail, useOwnerCandidates } from '@/components/flakes/flakeHelpers';
import { personLabel, REVIEW_LABEL, reviewSummary } from './ownershipHelpers';

/**
 * Owner, backup owner and periodic review for one case (WP16b). Saved on its
 * own, separately from the case's steps: ownership is metadata, not part of
 * the test (and not in its revisions or git-sync hash).
 */

function OwnerSelect({ id, label, value, current, candidates, disabled, onChange }: {
    id: string;
    label: string;
    value: number | null;
    current: CaseOwnership['owner'];
    candidates: { id: number; full_name?: string | null; email: string }[];
    disabled: boolean;
    onChange: (v: number | null) => void;
}) {
    // Someone who lost access is still shown (flagged) until reassigned.
    const options = candidates.map((p) => ({ id: p.id, label: p.full_name || p.email }));
    if (current && !options.some((o) => o.id === current.id)) options.unshift({ id: current.id, label: personLabel(current) });
    return (
        <label htmlFor={id} className="flex min-w-[200px] flex-1 flex-col gap-1 text-xs font-semibold text-ink-soft">
            {label}
            <select id={id} disabled={disabled} value={value ?? ''}
                onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
                className="h-10 rounded-xl border border-border bg-white px-3 text-sm font-normal text-ink">
                <option value="">Nobody</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </select>
            {current?.state === 'no_access' && (
                <span className="font-normal text-pastel-ink-peach">{personLabel(current)} — pick someone else.</span>
            )}
        </label>
    );
}

export function CaseOwnershipPanel({ caseId, projectId }: { caseId: number; projectId: number }) {
    const queryClient = useQueryClient();
    const key = ['case-ownership', caseId];
    const { data, isLoading, error } = useQuery({ queryKey: key, queryFn: () => getCaseOwnership(caseId) });
    const { data: people } = useOwnerCandidates(projectId);

    const onDone = (view: CaseOwnership) => {
        queryClient.setQueryData(key, view);
        queryClient.invalidateQueries({ queryKey: ['test-review', projectId] });
    };
    const save = useMutation({
        mutationFn: (body: { owner_user_id?: number | null; backup_owner_user_id?: number | null }) => setCaseOwnership(caseId, body),
        onSuccess: (view) => { onDone(view); toast.success('Ownership saved'); },
        onError: (err) => toast.error(`Could not save ownership: ${errorDetail(err)}`),
    });
    const reviewed = useMutation({
        mutationFn: () => markCaseReviewed(caseId),
        onSuccess: (view) => { onDone(view); toast.success('Marked reviewed'); },
        onError: (err) => toast.error(`Could not mark reviewed: ${errorDetail(err)}`),
    });

    if (isLoading) return <Loader2 className="h-4 w-4 animate-spin" aria-label="Loading ownership" />;
    if (error || !data) return <p className="text-sm text-ink-soft">Ownership unavailable: {errorDetail(error)}</p>;
    const status = REVIEW_LABEL[data.review_status];
    const busy = save.isPending || reviewed.isPending;

    return (
        <section aria-labelledby={`ownership-${caseId}`} className="mb-8 flex flex-col gap-4 rounded-3xl border border-border bg-white p-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 id={`ownership-${caseId}`} className="text-sm font-semibold text-ink">Ownership and review</h3>
                <Pill tone={status.tone}>{status.label}</Pill>
            </div>
            <div className="flex flex-wrap gap-3">
                <OwnerSelect id={`owner-${caseId}`} label="Owner" value={data.owner?.id ?? null} current={data.owner}
                    candidates={people ?? []} disabled={busy} onChange={(v) => save.mutate({ owner_user_id: v })} />
                <OwnerSelect id={`backup-${caseId}`} label="Backup owner" value={data.backup_owner?.id ?? null}
                    current={data.backup_owner} candidates={people ?? []} disabled={busy}
                    onChange={(v) => save.mutate({ backup_owner_user_id: v })} />
            </div>
            <div className="flex flex-wrap items-end justify-between gap-3">
                <div className="flex flex-col gap-1">
                    <Eyebrow>Last review</Eyebrow>
                    <span className="text-sm text-ink">{reviewSummary(data)}</span>
                    {data.last_reviewed_by && <span className="text-xs text-ink-soft">by {personLabel(data.last_reviewed_by)}</span>}
                </div>
                {data.review_status !== 'not_required' && (
                    <Button type="button" variant="outline" disabled={busy} onClick={() => reviewed.mutate()}>
                        <CheckCircle2 className="mr-1 h-4 w-4" /> Mark reviewed
                    </Button>
                )}
            </div>
            <p className="text-xs text-ink-soft">
                The review interval comes from the module settings (every {data.review_interval_days || '—'} days here).
                Owners can be emailed when this test first fails after passing, if the project turns that on.
            </p>
        </section>
    );
}
