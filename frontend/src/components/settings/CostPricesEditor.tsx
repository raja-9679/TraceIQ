import { useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { PriceRow, rowsFromTable, tableFromRows } from './costPrices';

/** Structured editor for COST_LLM_PRICES: one row per model, prices per 1M
 * input / output tokens. Reports the table as JSON text, or an error. */
export default function CostPricesEditor({ value, onChange }: {
    value: unknown;
    onChange: (json: string, error: string | null) => void;
}) {
    const [rows, setRows] = useState<PriceRow[]>(() => {
        const r = rowsFromTable(value);
        return r.length ? r : [{ model: '', input: '', output: '' }];
    });

    const emit = (next: PriceRow[]) => {
        setRows(next);
        const out = tableFromRows(next);
        if ('error' in out) onChange('', out.error);
        else onChange(Object.keys(out.table).length ? JSON.stringify(out.table) : '', null);
    };
    const set = (i: number, patch: Partial<PriceRow>) => emit(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

    return (
        <div className="flex w-full flex-col gap-2">
            <div className="hidden grid-cols-[1fr_7rem_7rem_auto] gap-2 text-xs font-medium text-ink-soft sm:grid">
                <span>Model (as shown in AI usage)</span><span>Input / 1M</span><span>Output / 1M</span><span />
            </div>
            {rows.map((r, i) => (
                <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_7rem_7rem_auto]">
                    <Input className={inputClass} aria-label={`Model ${i + 1}`} placeholder="model name" value={r.model}
                        onChange={(e) => set(i, { model: e.target.value })} />
                    <Input className={inputClass} aria-label={`Input price per million tokens, model ${i + 1}`} inputMode="decimal"
                        placeholder="0.00" value={r.input} onChange={(e) => set(i, { input: e.target.value })} />
                    <Input className={inputClass} aria-label={`Output price per million tokens, model ${i + 1}`} inputMode="decimal"
                        placeholder="0.00" value={r.output} onChange={(e) => set(i, { output: e.target.value })} />
                    <Button variant="outline" onClick={() => emit(rows.filter((_, j) => j !== i))} disabled={rows.length === 1 && !r.model}>
                        <Trash2 className="h-4 w-4" /> Remove
                    </Button>
                </div>
            ))}
            <Button variant="outline" className="self-start" onClick={() => setRows([...rows, { model: '', input: '', output: '' }])}>
                <Plus className="h-4 w-4" /> Add model
            </Button>
        </div>
    );
}
