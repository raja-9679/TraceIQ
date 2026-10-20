// Validated repairs (WP16c, decision V4) — pure helpers for the review UI.
// The backend (proposal.validation_required) stays the authority; this only
// explains a disabled Accept before anyone clicks it.
import type { Tone } from '@/components/redesign/kinds';

export type ValidationStatus = 'validating' | 'validated' | 'validation_failed' | null;

export interface ValidationConditions {
    browser?: string | null;
    device?: string | null;
    environment_id?: number | null;
    source_run_id?: number | null;
    matches_source?: boolean;
    selector?: string | null;
}

export interface ValidationFields {
    status: string;
    validation_status?: ValidationStatus;
    validation_run_id?: number | null;
    validation_conditions?: ValidationConditions | null;
    validation_detail?: string | null;
    validation_required?: boolean | null;
    protected_suite_id?: number | null;
}

export function validationLabel(status: ValidationStatus | undefined): { label: string; tone: Tone } {
    switch (status) {
        case 'validating': return { label: 'Validating', tone: 'lavender' };
        case 'validated': return { label: 'Validated', tone: 'mint' };
        case 'validation_failed': return { label: 'Validation failed', tone: 'pink' };
        default: return { label: 'Not validated', tone: 'neutral' };
    }
}

/** Why Accept is disabled, or null. `selector` = the heal candidate picked now. */
export function acceptBlock(p: ValidationFields, selector?: string | null): string | null {
    if (p.status !== 'pending' || !p.validation_required) return null;
    const why = p.protected_suite_id != null
        ? `Suite #${p.protected_suite_id} is protected, so this change`
        : 'This workspace requires that a change';
    switch (p.validation_status) {
        case 'validated': {
            const ran = p.validation_conditions?.selector;
            if (selector && ran && selector !== ran) {
                return `The validation run used ${ran}. Validate the selector you picked before applying it.`;
            }
            return null;
        }
        case 'validating':
            return 'The validation run has not finished yet.';
        case 'validation_failed':
            return `${why} must pass a validation run, and the last one did not pass. Fix it, validate again, or reject it.`;
        default:
            return `${why} must pass a validation run before it can be accepted.`;
    }
}

export function conditionsText(c: ValidationConditions | null | undefined): string {
    if (!c) return '';
    const parts = [c.browser || 'chromium'];
    if (c.device) parts.push(c.device);
    parts.push(c.environment_id != null ? `environment #${c.environment_id}` : 'default environment');
    return parts.join(' · ');
}

/** Case proposals can be validated only when they change steps of an existing case. */
export function caseValidatable(p: { action: string; target_case_id: number | null; payload: Record<string, unknown> | null }): boolean {
    return p.action === 'update' && p.target_case_id != null && !!p.payload
        && ('steps' in p.payload || !!p.payload.executor);
}

/** API errors carry a string detail, or (new policy rules) an object with `message`. */
export function detailText(detail: unknown, fallback = 'Request failed'): string {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && typeof (detail as { message?: unknown }).message === 'string') {
        return (detail as { message: string }).message;
    }
    return fallback;
}

export const anyValidating = (items: { validation_status?: ValidationStatus }[] | undefined) =>
    !!items?.some((p) => p.validation_status === 'validating');
