import type { CellSpec } from '@/api/accessMatrix';
import { EXPECT_TONE } from './model';
import { EXPECT_LABEL } from './grid';

interface Props {
    cell: CellSpec | undefined;
    rowLabel: string;
    columnLabel: string;
    onCycle: () => void;
    onStatus: (text: string) => void;
}

/** One editable grid cell: click cycles allow → deny → skip; the optional
 * status is the exact status for allow, an extra deny status for deny. */
export default function ExpectCell({ cell, rowLabel, columnLabel, onCycle, onStatus }: Props) {
    const expect = cell?.expect ?? 'skip';
    const who = `${rowLabel || 'row'} as ${columnLabel}`;
    return (
        <div className="flex flex-col items-stretch gap-1 min-w-[96px]">
            <button type="button" onClick={onCycle}
                aria-label={`Expectation for ${who}: ${EXPECT_LABEL[expect]}. Click to change.`}
                className={`h-8 rounded-md border text-xs font-semibold ${EXPECT_TONE[expect]} border-slate-200 hover:ring-1 hover:ring-indigo-300`}>
                {EXPECT_LABEL[expect]}
            </button>
            {expect !== 'skip' && (
                <label className="flex items-center gap-1 text-[10px] text-slate-500">
                    <span>{expect === 'allow' ? 'Status' : '+deny'}</span>
                    <input key={expect} type="number" min={100} max={599} inputMode="numeric"
                        aria-label={`${expect === 'allow' ? 'Exact status' : 'Extra deny status'} for ${who}`}
                        className="w-14 h-6 rounded border border-slate-200 px-1 text-[11px]"
                        defaultValue={cell?.status ?? ''} placeholder="any"
                        onBlur={(e) => onStatus(e.target.value)} />
                </label>
            )}
        </div>
    );
}
