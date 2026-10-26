// Pure helpers for the WP8 fleet / on-call / network UI. Kept out of the
// component files so fast-refresh lint stays happy and they are unit-testable.

/** "eu-west, US " → ["eu-west", "us"]; blank → null (no regions). */
export function parseRegions(raw: string): string[] | null {
    const out: string[] = [];
    for (const part of raw.split(',')) {
        const r = part.trim().toLowerCase();
        if (r && !out.includes(r)) out.push(r);
    }
    return out.length ? out : null;
}

/** Comma/whitespace-separated CIDRs → list; blank → null (no restriction). */
export function parseCidrList(raw: string): string[] | null {
    const out = raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
    return out.length ? out : null;
}

/** Hostnames one per line or comma-separated. */
export function parseHostList(raw: string): string[] {
    return raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
}

export interface PromQuery {
    name: string;
    query: string;
}

/** "CPU | rate(x[1m])" per line ↔ [{name, query}]. A line without "|" is a bare query. */
export function parsePromQueries(raw: string): PromQuery[] {
    const out: PromQuery[] = [];
    raw.split('\n').forEach((line) => {
        const text = line.trim();
        if (!text) return;
        const idx = text.indexOf('|');
        if (idx === -1) {
            out.push({ name: `query ${out.length + 1}`, query: text });
        } else {
            const name = text.slice(0, idx).trim() || `query ${out.length + 1}`;
            const query = text.slice(idx + 1).trim();
            if (query) out.push({ name, query });
        }
    });
    return out;
}

export function formatPromQueries(queries: unknown): string {
    if (!Array.isArray(queries)) return '';
    return queries
        .map((q: unknown) => {
            if (typeof q === 'string') return q;
            const o = (q ?? {}) as Partial<PromQuery>;
            return `${o.name ?? ''} | ${o.query ?? ''}`;
        })
        .join('\n');
}

/** Seconds → "12s" / "4m" / "3h" / "2d". */
export function formatAge(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.round(s / 60)}m`;
    if (s < 86400) return `${Math.round(s / 3600)}h`;
    return `${Math.round(s / 86400)}d`;
}

/** `datetime-local` value (browser-local) → ISO UTC string, or null. */
export function localInputToIso(value: string): string | null {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

/** Naive-UTC ISO from the backend → `datetime-local` value in the browser's zone. */
export function isoToLocalInput(iso: string | null | undefined): string {
    if (!iso) return '';
    const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
    if (Number.isNaN(d.getTime())) return '';
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export interface QueueGroup<J> {
    /** null = side jobs (crawls, page checks…) that belong to no run. */
    runId: number | null;
    jobs: J[];
    claimed: number;
    oldestWaitingSeconds: number | null;
}

/** Waiting jobs grouped by run, oldest-waiting group first. Clearing works per
 * run (it cancels the run), so the screen shows runs, not single jobs. */
export function groupQueueByRun<J extends { run_id: number | null; claimed: boolean; waiting_seconds: number | null }>(
    jobs: J[],
): QueueGroup<J>[] {
    const byRun = new Map<number | null, QueueGroup<J>>();
    for (const j of jobs) {
        let g = byRun.get(j.run_id);
        if (!g) {
            g = { runId: j.run_id, jobs: [], claimed: 0, oldestWaitingSeconds: null };
            byRun.set(j.run_id, g);
        }
        g.jobs.push(j);
        if (j.claimed) g.claimed += 1;
        if (j.waiting_seconds !== null && (g.oldestWaitingSeconds === null || j.waiting_seconds > g.oldestWaitingSeconds)) {
            g.oldestWaitingSeconds = j.waiting_seconds;
        }
    }
    return [...byRun.values()].sort((a, b) => (b.oldestWaitingSeconds ?? 0) - (a.oldestWaitingSeconds ?? 0));
}
