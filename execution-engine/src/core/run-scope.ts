/**
 * Run-scoped template values: {{run.*}}, {{unique.<name>}} and the seeded
 * random stream behind {{fake.*}}.
 *
 * One RunScope exists per case attempt. Everything here is derived from the
 * job (run id, the backend's short token, case id, data row, attempt) so two
 * workers running different cases of the same run can never produce the same
 * {{unique.x}}, while the same case attempt always sees one stable value.
 */
import { createHash } from 'crypto';

/** `settings.run` as dispatched by the backend (`run_context`). */
export interface RunContextPayload {
    id?: number;
    short?: string;
    started_at?: string;
    data_seed?: string | null;
}

/** 32-bit FNV-1a — only used to turn a seed string into PRNG state. */
export function hash32(text: string): number {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i++) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h >>> 0;
}

/** mulberry32: tiny, well-distributed, and identical on every platform. */
export function mulberry32(seed: number): () => number {
    let a = seed >>> 0;
    return () => {
        a = (a + 0x6d2b79f5) >>> 0;
        let t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

/**
 * Random stream for {{fake.*}} when a data seed is set. Keyed by
 * (seed, case, row) only — NOT the attempt or the run — because the point is
 * that replaying a failing run with its recorded seed types the same data.
 */
export function seededRng(seed: string, caseId: any, rowIndex: number | null | undefined): () => number {
    return mulberry32(hash32(`${seed}|case:${caseId}|row:${rowIndex ?? -1}`));
}

export class RunScope {
    /** {{run.KEY}} values. */
    readonly vars: Record<string, string>;
    /** Present only when a data seed is in effect. */
    readonly fakeRng?: () => number;
    private readonly uniqueBase: string;
    private readonly uniques = new Map<string, string>();

    constructor(opts: {
        run?: RunContextPayload | null;
        runId: number | string;
        caseId: number | string;
        rowIndex?: number | null;
        workerId?: string;
        attempt?: number;
    }) {
        const run = opts.run || {};
        const runId = String(run.id ?? opts.runId ?? '');
        // The backend sends an HMAC-derived token; base36 of the id is only a
        // fallback for jobs from an older backend.
        const short = String(run.short || Number(runId || 0).toString(36));
        const row = typeof opts.rowIndex === 'number' ? opts.rowIndex : null;
        this.vars = {
            id: runId,
            short,
            worker: String(opts.workerId || process.env.HOSTNAME || 'worker'),
            case: String(opts.caseId ?? ''),
            // 1-based like the "[row N]" result name; 0 = not data-driven.
            row: String(row === null ? 0 : row + 1),
            started_at: String(run.started_at || new Date().toISOString()),
            attempt: String(opts.attempt || 1),
        };
        this.uniqueBase = `${runId}|${short}|${this.vars.case}|${row ?? -1}|${this.vars.attempt}`;
        const seed = run.data_seed;
        if (seed !== undefined && seed !== null && String(seed) !== '') {
            this.fakeRng = seededRng(String(seed), opts.caseId, row);
        }
    }

    /**
     * {{unique.<name>}}: 12 lowercase alphanumerics starting with a letter
     * (valid in usernames, emails, slugs). Stable for the attempt, different
     * per run/case/row/attempt — a retry must not collide with the record its
     * first attempt already created. Never seeded, for the same reason.
     */
    unique(name: string): string {
        const hit = this.uniques.get(name);
        if (hit) return hit;
        const digest = createHash('sha256').update(`${this.uniqueBase}|${name}`).digest();
        const alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789';
        let out = String.fromCharCode(97 + (digest[0] % 26));
        for (let i = 1; out.length < 12; i++) out += alphabet[digest[i] % 36];
        this.uniques.set(name, out);
        return out;
    }

    /** Scope for one case of a job (web, continuous or mobile). */
    static forCase(job: any, testCase: any, workerId?: string, attempt = 1): RunScope {
        return new RunScope({
            run: job?.settings?.run,
            runId: job?.run_id,
            caseId: testCase?.id,
            rowIndex: typeof testCase?.row_index === 'number' ? testCase.row_index : null,
            workerId,
            attempt,
        });
    }
}
