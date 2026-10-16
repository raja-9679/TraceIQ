import type { SuiteSettingsBlob } from '@/components/evidence/evidenceHelpers';

// Suite retry policy, stored in the suite settings blob and inherited down
// the suite chain like other execution flags. The backend clamps on dispatch
// (backend/app/worker.py retry_count): 0..5 retries, default 2.

export type RetryMode = 'inherit' | 'on' | 'off';
export const RETRY_CHOICES = [1, 2, 3, 5] as const;
export const DEFAULT_RETRIES = 2;
export const MAX_RETRIES = 5;

export function retryMode(own: SuiteSettingsBlob | null | undefined): RetryMode {
    const v = own?.auto_retry;
    return v === true ? 'on' : v === false ? 'off' : 'inherit';
}

export function retryCount(blob: SuiteSettingsBlob | null | undefined): number {
    const n = Number(blob?.max_retries);
    if (!Number.isFinite(n)) return DEFAULT_RETRIES;
    return Math.max(0, Math.min(MAX_RETRIES, Math.trunc(n)));
}

/** The suite's own settings after choosing a mode (and, when on, a count). */
export function applyRetry(own: SuiteSettingsBlob | null | undefined, mode: RetryMode, count?: number): SuiteSettingsBlob {
    const next: SuiteSettingsBlob = { ...(own || {}) };
    if (mode === 'inherit') {
        // Remove both keys so the parent module's policy applies untouched.
        delete next.auto_retry;
        delete next.max_retries;
    } else if (mode === 'off') {
        next.auto_retry = false;
        delete next.max_retries;
    } else {
        next.auto_retry = true;
        next.max_retries = Math.max(1, Math.min(MAX_RETRIES, count ?? retryCount(own)));
    }
    return next;
}

/** What a suite left on "inherit" actually gets, for the card's status line. */
export function inheritedSummary(effective: SuiteSettingsBlob | null | undefined): string {
    if (effective?.auto_retry === true) {
        const n = retryCount(effective);
        return `Currently on (up to ${n} ${n === 1 ? 'retry' : 'retries'}), inherited from a parent module.`;
    }
    return 'Currently off: no parent module turns retries on.';
}
