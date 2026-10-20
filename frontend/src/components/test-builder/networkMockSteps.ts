// Form helpers for the network stubbing steps (WP15d): mock-response (single
// response or a `sequence`) and har-replay. Mirrors the /api/step-types
// catalog entries in backend/app/api/agent_reference.py.

export const NETWORK_MOCK_STEP_TYPES = ['mock-response', 'har-replay'] as const;

export const isNetworkMockStep = (t: string): boolean =>
    (NETWORK_MOCK_STEP_TYPES as readonly string[]).includes(t);

export const NETWORK_MOCK_LABELS: Record<string, string> = {
    'mock-response': 'Mock Response',
    'har-replay': 'Replay HAR',
};

export const SEQUENCE_EXAMPLE = '[\n  { "status": 503 },\n  { "status": 200, "json": { "ok": true } }\n]';

export function sequenceToText(seq: unknown): string {
    return Array.isArray(seq) ? JSON.stringify(seq, null, 2) : '';
}

/** Parse the sequence editor: a non-empty JSON list of response objects. */
export function textToSequence(text: string): { sequence?: Record<string, unknown>[]; error?: string } {
    let parsed: unknown;
    try {
        parsed = JSON.parse(text);
    } catch {
        return { error: 'Not valid JSON yet.' };
    }
    if (!Array.isArray(parsed) || parsed.length === 0) return { error: 'Enter a list with at least one response.' };
    if (parsed.some((e) => !e || typeof e !== 'object' || Array.isArray(e))) {
        return { error: 'Each entry must be an object like { "status": 200, "json": {…} }.' };
    }
    return { sequence: parsed as Record<string, unknown>[] };
}

/** params for switching a mock between single-response and sequence modes. */
export function withMode(params: Record<string, unknown>, mode: 'single' | 'sequence'): Record<string, unknown> {
    const next = { ...params };
    if (mode === 'single') {
        delete next.sequence;
        delete next.after_sequence;
    } else if (!Array.isArray(next.sequence)) {
        next.sequence = [{ status: Number(next.status) || 200 }];
    }
    return next;
}

/** "12" -> 12; anything else -> undefined (param removed). */
export function harIdParam(raw: string): number | undefined {
    return /^\d+$/.test(raw.trim()) ? Number(raw.trim()) : undefined;
}
