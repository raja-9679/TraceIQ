import type { MatrixCellResult, MatrixRun } from '@/api/accessMatrix';
import { cellIndex, columnLabel, expectedText, observedText, VERDICT_TONE } from './model';
import { failureKind, rowTargetText, summaryParts, VERDICT_LABEL } from './grid';

interface Props {
    run: MatrixRun;
    selectedCellId: number | null;
    onSelect: (cell: MatrixCellResult) => void;
}

/** Expected vs observed per (target, identity) for one execution, built from
 * the run's snapshot so a later edit of the matrix can't relabel old results. */
export default function ResultsGrid({ run, selectedCellId, onSelect }: Props) {
    const snap = run.snapshot;
    const index = cellIndex(run.cells);
    if (!snap) return null;
    const names = snap.persona_names || {};

    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-xs" aria-label="Run summary">
                {summaryParts(run.summary).map(p => (
                    <span key={p.verdict} className={`rounded-md border px-2 py-0.5 font-semibold ${VERDICT_TONE[p.verdict]}`}>
                        {VERDICT_LABEL[p.verdict]}: {p.count}
                    </span>
                ))}
                {!!run.summary?.access_granted && (
                    <span className="font-semibold text-rose-700">{run.summary.access_granted} access granted that should be denied</span>
                )}
                {!!run.summary?.allow_regressions && (
                    <span className="font-semibold text-amber-700">{run.summary.allow_regressions} allow regression(s)</span>
                )}
            </div>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="w-full text-xs">
                    <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                        <tr>
                            <th scope="col" className="px-3 py-2 text-left font-semibold">Target</th>
                            {snap.columns.map(k => <th key={k} scope="col" className="px-2 py-2 text-center font-semibold">{columnLabel(k, names)}</th>)}
                        </tr>
                    </thead>
                    <tbody>
                        {snap.rows.map(row => (
                            <tr key={row.key} className="border-t border-slate-100">
                                <th scope="row" className="px-3 py-2 text-left font-normal">
                                    <div className="font-semibold text-slate-800">{row.label || row.key}</div>
                                    <div className="font-mono text-[10px] text-slate-500 break-all">{rowTargetText(row)}</div>
                                </th>
                                {snap.columns.map(k => {
                                    const c = index.get(`${row.key}|${k}`);
                                    if (!c) return <td key={k} className="px-2 py-2 text-center text-slate-300">skip</td>;
                                    const kind = failureKind(c.expect, c.verdict);
                                    return (
                                        <td key={k} className="px-1.5 py-1.5">
                                            <button type="button" onClick={() => onSelect(c)}
                                                aria-label={`${row.label || row.key} as ${columnLabel(k, names)}: ${VERDICT_LABEL[c.verdict]}. Show evidence.`}
                                                className={`w-full min-w-[110px] rounded-md border px-2 py-1.5 text-left ${VERDICT_TONE[c.verdict]} ${selectedCellId === c.id ? 'ring-2 ring-indigo-400' : ''}`}>
                                                <div className="font-bold">{VERDICT_LABEL[c.verdict]}{kind ? ` — ${kind}` : ''}</div>
                                                <div>expected {expectedText(c)}</div>
                                                <div>observed {observedText(c)}</div>
                                            </button>
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
