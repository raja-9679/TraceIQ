// Helpers for the wait-for-email / wait-for-webhook step editors. Defaults
// mirror execution-engine/src/core/test-hooks.ts.

export const EMAIL_EXTRACT_OPTIONS = [
    { value: 'link', label: 'First link' },
    { value: 'code', label: 'Code (regex)' },
    { value: 'body', label: 'Whole body' },
    { value: 'subject', label: 'Subject' },
    { value: 'none', label: 'Nothing — just wait' },
] as const;

const EMAIL_DEFAULT_VAR: Record<string, string> = {
    link: 'email_link', code: 'email_code', body: 'email_body', subject: 'email_subject',
};

/** Variable the email step stores into, or null when it stores nothing. */
export type HookParams = Record<string, unknown> | undefined;

/** Display string for a param value ('' when unset). */
export function str(value: unknown): string {
    return value === undefined || value === null ? '' : String(value);
}

export function emailVariable(params: HookParams): string | null {
    const mode = String(params?.extract || 'link');
    if (mode === 'none') return null;
    return String(params?.variableName || EMAIL_DEFAULT_VAR[mode] || 'email_link');
}

export function webhookVariable(params: HookParams): string {
    if (params?.variableName) return String(params.variableName);
    return params?.extract ? 'webhook_value' : 'webhook_body';
}

/** Error text for the "where" JSON field, or null when it is empty/valid. */
export function whereError(text: string): string | null {
    if (!text.trim()) return null;
    try {
        const parsed = JSON.parse(text);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            return 'Must be a JSON object, e.g. {"type": "order.paid"}';
        }
        return null;
    } catch {
        return 'Not valid JSON';
    }
}

export function whereToText(where: unknown): string {
    if (where === undefined || where === null || where === '') return '';
    return typeof where === 'string' ? where : JSON.stringify(where);
}
