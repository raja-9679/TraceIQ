import { useQuery } from '@tanstack/react-query';
import { accessMatrixApi } from '@/api/accessMatrix';
import { errorDetail } from './grid';
import { FINDING_STATUS_LABEL, normalizeStatus } from '@/components/security/findingStatus';
import type { FindingStatus } from '@/api/security';
import { Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';

interface Props {
    matrixId: number;
    /** Changes when a run finishes, so new findings show up without a reload. */
    refreshKey: number | null;
    onOpen: (matrixRunId: number, cellId: number) => void;
}

// WP7 finding workflow statuses; pre-WP7 rows (acknowledged/resolved) are
// mapped onto them by normalizeStatus.
const STATUS_TONE: Record<FindingStatus, Tone> = {
    open: 'peach',
    in_progress: 'butter',
    fixed_pending_verification: 'lavender',
    verified_fixed: 'mint',
    accepted_risk: 'pink',
    false_positive: 'neutral',
};

/** access_control findings this matrix raised, each linked to the latest run
 * whose cell granted the access (the evidence that created or reopened it). */
export default function MatrixFindings({ matrixId, refreshKey, onOpen }: Props) {
    const q = useQuery({
        queryKey: ['am-findings', matrixId, refreshKey],
        queryFn: () => accessMatrixApi.findings(matrixId),
    });
    if (q.isLoading) return null;

    return (
        <section aria-labelledby="am-findings" className="rounded-2xl border border-border p-4">
            <h3 id="am-findings" className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Security findings from this matrix</h3>
            <p className="mt-1.5 text-sm text-ink-soft">
                A DENY cell that was granted raises a high-severity access-control finding; it is marked fixed once a later run
                denies again and reopens on regression.
            </p>
            {q.error ? <p className="mt-2 text-xs text-pastel-ink-peach">{errorDetail(q.error)}</p>
                : !q.data?.length ? <p className="mt-2 text-sm text-ink-soft">None.</p>
                : (
                    <ul className="mt-3 space-y-2">
                        {q.data.map(f => (
                            <li key={f.id} className="flex flex-wrap items-start gap-2 text-sm">
                                <Pill tone={STATUS_TONE[normalizeStatus(f.status)] ?? STATUS_TONE.open}>{FINDING_STATUS_LABEL[normalizeStatus(f.status)] ?? f.status.replace(/_/g, ' ')}</Pill>
                                <span className="min-w-0 flex-1 text-ink">{f.title}</span>
                                <button type="button" className="text-xs font-semibold text-ink underline underline-offset-2"
                                    onClick={() => onOpen(f.matrix_run_id, f.cell_id)}>
                                    View evidence in run #{f.matrix_run_id}
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
        </section>
    );
}
