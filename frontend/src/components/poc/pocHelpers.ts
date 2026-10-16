// Pure helpers for the PoC report page and its public share view.

export const EXECUTOR_LABELS: Record<string, string> = {
    ui_playwright: 'Browser journeys',
    raw_playwright: 'Playwright specs',
    selenium: 'Selenium',
    api: 'API',
    load: 'Load',
    security: 'Security',
    mobile_appium: 'Mobile',
    manual: 'Manual',
};

export const executorLabel = (k: string) => EXECUTOR_LABELS[k] ?? k;

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Default range: the last 30 days, today included (the backend's default too). */
export function defaultRange(today: Date = new Date()): { start: string; end: string } {
    const start = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - 29));
    return { start: iso(start), end: iso(today) };
}

/** "1,234" / "—" for missing. */
export const fmtCount = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString());

/** A list of names, or a dash. */
export const fmtNames = (v: string[] | null | undefined, empty = 'None') => (v && v.length ? v.join(', ') : empty);

/** Currency codes are typed by the user: three letters, upper-cased. */
export function normaliseCurrency(v: string): string {
    return v.replace(/[^a-zA-Z]/g, '').slice(0, 3).toUpperCase();
}

/** Client-side check mirroring the backend (rate needs a code; minutes in (0, 480]). */
export function assumptionError(minutes: number, rate: number | null, currency: string): string | null {
    if (!(minutes > 0) || minutes > 480) return 'Minutes per execution must be above 0 and at most 480.';
    if (rate != null && (rate < 0 || rate > 100000)) return 'Hourly rate must be between 0 and 100000.';
    if (rate != null && !/^[A-Z]{3}$/.test(currency)) return 'Type a three-letter currency code with the rate, for example EUR.';
    if (currency && !/^[A-Z]{3}$/.test(currency)) return 'Currency must be a three-letter code.';
    return null;
}
