// `params.cleanup` on http-request / extract-value steps. Semantics live in
// execution-engine/src/core/suite-hooks.ts:buildCleanup.

export type CleanupMode = 'off' | 'auto' | 'custom';

export interface CleanupObject {
    method?: string;
    url?: string;
    [key: string]: unknown;
}

export function cleanupMode(spec: unknown): CleanupMode {
    if (spec === true) return 'auto';
    if (spec && typeof spec === 'object') return 'custom';
    return 'off';
}

/** The params.cleanup value for a mode, keeping a custom spec's fields. */
export function cleanupFor(mode: CleanupMode, current: unknown): true | CleanupObject | undefined {
    if (mode === 'off') return undefined;
    if (mode === 'auto') return true;
    return current && typeof current === 'object' ? (current as CleanupObject) : { method: 'DELETE', url: '' };
}

/** What `cleanup: true` deletes, per step type. */
export function autoCleanupHint(stepType: string): string {
    return stepType === 'extract-value'
        ? 'Deletes the extracted value (it must be a URL).'
        : "Deletes the URL in the response's Location header.";
}
