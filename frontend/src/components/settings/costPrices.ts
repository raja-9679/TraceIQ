// The COST_LLM_PRICES instance setting as editable rows. Mirrors
// backend/app/services/cost.py validate_llm_prices (which re-checks on save).

export interface PriceRow {
    model: string;
    input: string;
    output: string;
}

export type PriceTable = Record<string, { input_per_1m: number; output_per_1m: number }>;

export function rowsFromTable(value: unknown): PriceRow[] {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value as Record<string, { input_per_1m?: unknown; output_per_1m?: unknown } | null>).map(([model, p]) => ({
        model,
        input: p?.input_per_1m === undefined || p?.input_per_1m === null ? '' : String(p.input_per_1m),
        output: p?.output_per_1m === undefined || p?.output_per_1m === null ? '' : String(p.output_per_1m),
    }));
}

/** Rows → table. Fully empty rows are dropped; anything half-filled is an error. */
export function tableFromRows(rows: PriceRow[]): { table: PriceTable } | { error: string } {
    const table: PriceTable = {};
    const seen = new Set<string>();
    for (const [i, r] of rows.entries()) {
        const model = r.model.trim();
        if (!model && !r.input.trim() && !r.output.trim()) continue;
        if (!model) return { error: `Row ${i + 1}: model name is required` };
        if (seen.has(model.toLowerCase())) return { error: `${model} is listed twice` };
        seen.add(model.toLowerCase());
        const input = Number(r.input.trim());
        const output = Number(r.output.trim());
        if (r.input.trim() === '' || r.output.trim() === '' || !Number.isFinite(input) || !Number.isFinite(output))
            return { error: `${model}: enter both prices as numbers` };
        if (input < 0 || output < 0) return { error: `${model}: prices cannot be negative` };
        table[model] = { input_per_1m: input, output_per_1m: output };
    }
    return { table };
}
