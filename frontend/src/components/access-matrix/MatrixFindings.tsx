import { useQuery } from '@tanstack/react-query';
import { accessMatrixApi } from '@/api/accessMatrix';
import { errorDetail } from './grid';

interface Props {
    matrixId: number;
    /** Changes when a run finishes, so new findings show up without a reload. */
    refreshKey: number | null;
    onOpen: (matrixRunId: number, cellId: number) => void;
}

const STATUS_TONE: Record<string, string> = {
    open: 'bg-rose-50 text-rose-700 border-rose-200',
    acknowledged: 'bg-amber-50 text-amber-700 border-amber-200',
    resolved: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    false_positive: 'bg-slate-50 text-slate-600 border-slate-200',
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
        <section aria-labelledby="am-findings" className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 id="am-findings" className="text-sm font-bold text-slate-800">Security findings from this matrix</h3>
            <p className="mt-1 text-xs text-slate-500">
                A DENY cell that was granted raises a high-severity access-control finding; it resolves when a later run
                denies again and reopens on regression.
            </p>
            {q.error ? <p className="mt-2 text-xs text-rose-600">{errorDetail(q.error)}</p>
                : !q.data?.length ? <p className="mt-2 text-xs text-slate-500">None.</p>
                : (
                    <ul className="mt-3 space-y-2">
                        {q.data.map(f => (
                            <li key={f.id} className="flex flex-wrap items-start gap-2 text-sm">
                                <span className={`rounded border px-1.5 text-[11px] font-semibold ${STATUS_TONE[f.status] ?? STATUS_TONE.open}`}>{f.status.replace('_', ' ')}</span>
                                <span className="min-w-0 flex-1 text-slate-800">{f.title}</span>
                                <button type="button" className="text-xs font-medium text-indigo-600 hover:underline"
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
