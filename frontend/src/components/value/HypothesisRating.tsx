import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { hypothesisFeedbackApi, type HypothesisFeedbackRow, type HypothesisVerdict, type RunHypothesisFeedback } from '@/api/valueReport';
import { errorDetail } from '@/components/flakes/flakeHelpers';
import { applyVote, nextVote } from './valueHelpers';

// 👍 / 👎 on one failure-analysis hypothesis (WP16d). The ratings feed the
// Value report's "AI failure-analysis accuracy" — a reviewed sample. Editors
// rate; everyone else sees the counts. Clicking your current verdict clears it.

const btn = 'inline-flex min-h-7 items-center gap-1 rounded-full border px-2.5 text-[11px] font-semibold transition-colors disabled:opacity-50';

function Buttons({ row, canRate, busy, onVote }: {
    row: HypothesisFeedbackRow;
    canRate: boolean;
    busy: boolean;
    onVote: (v: HypothesisVerdict) => void;
}) {
    const opt = (v: HypothesisVerdict, icon: string, label: string, count: number) => {
        const on = row.mine === v;
        return (
            <button
                type="button"
                className={`${btn} ${on ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-secondary'}`}
                aria-pressed={on}
                disabled={!canRate || busy}
                onClick={() => onVote(v)}
                title={canRate ? `Mark this hypothesis ${label.toLowerCase()}` : 'Editors can rate hypotheses'}
            >
                <span aria-hidden>{icon}</span> {label}{count > 0 ? ` (${count})` : ''}
            </button>
        );
    };
    return (
        <span className="mt-1 inline-flex flex-wrap items-center gap-1.5" role="group" aria-label="Was this hypothesis right?">
            {opt('correct', '👍', 'Correct', row.correct)}
            {opt('incorrect', '👎', 'Incorrect', row.incorrect)}
        </span>
    );
}

export function HypothesisRating({ runId, resultId, index }: { runId: number; resultId: number; index: number }) {
    const qc = useQueryClient();
    const key = ['hypothesis-feedback', runId];
    const { data } = useQuery({ queryKey: key, queryFn: () => hypothesisFeedbackApi.forRun(runId) });
    const rows = data?.results[String(resultId)];
    const row = rows?.find((r) => r.index === index);
    const vote = useMutation({
        mutationFn: (v: HypothesisVerdict | null) => (v
            ? hypothesisFeedbackApi.rate(runId, resultId, { index, hypothesis_key: row!.hypothesis_key, verdict: v })
            : hypothesisFeedbackApi.clear(runId, resultId, index, row!.hypothesis_key)),
        onMutate: (v) => {
            const prev = qc.getQueryData<RunHypothesisFeedback>(key);
            if (prev && rows) {
                qc.setQueryData<RunHypothesisFeedback>(key, {
                    ...prev, results: { ...prev.results, [String(resultId)]: applyVote(rows, index, v) },
                });
            }
            return { prev };
        },
        onError: (e, _v, ctx) => {
            if (ctx?.prev) qc.setQueryData(key, ctx.prev);
            toast.error(errorDetail(e));
        },
        onSettled: () => qc.invalidateQueries({ queryKey: key }),
    });
    if (!data || !row || data.is_validation) return null;
    if (!data.can_rate && row.correct + row.incorrect === 0) return null;
    return <Buttons row={row} canRate={data.can_rate} busy={vote.isPending} onVote={(v) => vote.mutate(nextVote(row.mine, v))} />;
}
