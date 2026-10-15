import type { MatrixRow, ProjectCase } from '@/api/accessMatrix';
import { Button } from '@/components/ui/button';
import ExpectCell from './ExpectCell';
import RowEditor from './RowEditor';
import { blankRow } from './model';
import { cycleCell, setCellStatus } from './grid';

interface Props {
    rows: MatrixRow[];
    columns: { key: string; label: string }[];
    personas: { id: number; name: string }[];
    cases: ProjectCase[];
    onChange: (rows: MatrixRow[]) => void;
}

/** Rows are targets, columns are identities; each cell is an expectation. */
export default function GridEditor({ rows, columns, personas, cases, onChange }: Props) {
    const update = (i: number, row: MatrixRow) => onChange(rows.map((r, j) => (j === i ? row : r)));

    return (
        <div className="space-y-3">
            {columns.length === 0 ? (
                <p className="text-sm text-slate-500">Choose at least one persona (or the Anonymous column) above to start the grid.</p>
            ) : (
                <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                    <table className="w-full text-sm">
                        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                            <tr>
                                <th scope="col" className="px-3 py-2 text-left font-semibold">Target</th>
                                {columns.map(c => <th key={c.key} scope="col" className="px-2 py-2 text-center font-semibold">{c.label}</th>)}
                            </tr>
                        </thead>
                        <tbody>
                            {rows.length === 0 && (
                                <tr><td colSpan={columns.length + 1} className="px-3 py-6 text-center text-slate-400">No rows yet.</td></tr>
                            )}
                            {rows.map((row, i) => (
                                <tr key={row.key} className="border-t border-slate-100 align-top">
                                    <td className="px-3 py-3">
                                        <RowEditor row={row} personas={personas} cases={cases}
                                            onChange={(r) => update(i, r)}
                                            onRemove={() => onChange(rows.filter((_, j) => j !== i))} />
                                    </td>
                                    {columns.map(c => (
                                        <td key={c.key} className="px-2 py-3">
                                            <ExpectCell cell={row.cells[c.key]} rowLabel={row.label || row.key} columnLabel={c.label}
                                                onCycle={() => update(i, cycleCell(row, c.key))}
                                                onStatus={(t) => update(i, setCellStatus(row, c.key, t))} />
                                        </td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
            <Button type="button" variant="outline" size="sm" disabled={columns.length === 0}
                onClick={() => onChange([...rows, blankRow(rows)])}>
                Add row
            </Button>
        </div>
    );
}
