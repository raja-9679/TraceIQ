// Money display for the cost pages. null = not priced (no currency set, or no
// price for that usage) and is shown as such — never as 0.

export const NOT_PRICED = 'not priced';

export function formatMoney(amount: number | null | undefined, currency: string | null | undefined): string {
    if (amount === null || amount === undefined || !currency) return NOT_PRICED;
    try {
        return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount);
    } catch {
        // An unknown-to-the-browser code still renders, just without a symbol.
        return `${amount.toFixed(2)} ${currency}`;
    }
}

export function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
    return n.toLocaleString();
}

/** Pill tone for a budget position (this month). */
export function budgetTone(pct: number | null | undefined): 'mint' | 'butter' | 'peach' | 'neutral' {
    if (pct === null || pct === undefined) return 'neutral';
    if (pct >= 100) return 'peach';
    if (pct >= 80) return 'butter';
    return 'mint';
}

/** Parse a budget input: '' clears it, anything else must be a positive number. */
export function parseBudget(raw: string): { value: number | null } | { error: string } {
    const s = raw.trim();
    if (s === '') return { value: null };
    const n = Number(s);
    if (!Number.isFinite(n) || n <= 0) return { error: 'Enter a positive amount, or leave it empty for no budget' };
    return { value: Math.round(n * 100) / 100 };
}
