// Talking to the TraceIQ Recorder browser extension
// (integrations/browser-recorder).
//
// The extension's content script is the only part of it a page can reach, and
// it is reached with window.postMessage on our own origin. The extension holds
// no API key: it records in another tab and hands the steps back to this page,
// which adds them to the open test as unsaved changes — so what gets saved is
// still decided by the signed-in user's own permissions, and the user reviews
// the steps first.
import { newId } from '@/lib/ids';
import type { TestStep } from '@/components/test-builder/StepComponent';

export type RecordedStep = TestStep & { intent?: string };

export type RecorderMessage =
    | { type: 'pong'; version: string; trusted: boolean; recording: boolean; pending: number }
    | { type: 'starting' | 'needs-approval' | 'started' | 'denied' | 'ok' }
    | { type: 'steps'; steps: RecordedStep[] }
    | { type: 'error'; error: string };

// What the recorder can produce. Anything else is dropped: the extension is a
// separate program, and an unknown step type would only fail later, in a run.
const RECORDABLE = new Set<string>([
    'goto', 'click', 'fill', 'check', 'uncheck', 'select-option', 'press-key', 'drag-and-drop',
    'hover', 'switch-frame', 'expect-visible', 'expect-text', 'expect-url', 'expect-title',
]);
export const MAX_RECORDED_STEPS = 500;

const text = (v: unknown, max: number): string | undefined =>
    typeof v === 'string' && v.length > 0 ? v.slice(0, max) : undefined;

/** Rebuilds recorded steps field by field, with fresh ids. */
export function cleanRecordedSteps(raw: unknown): RecordedStep[] {
    if (!Array.isArray(raw)) return [];
    const out: RecordedStep[] = [];
    for (const item of raw.slice(0, MAX_RECORDED_STEPS)) {
        if (!item || typeof item !== 'object') continue;
        const s = item as Record<string, unknown>;
        if (typeof s.type !== 'string' || !RECORDABLE.has(s.type)) continue;
        const intent = text(s.intent, 200);
        out.push({
            id: newId(),
            type: s.type as TestStep['type'],
            selector: text(s.selector, 1000) ?? '',
            value: text(s.value, 2000) ?? '',
            ...(intent ? { intent } : {}),
        });
    }
    return out;
}

/** Names used in `{{secret.NAME}}` values — passwords the recorder left out. */
export function secretsNeeded(steps: RecordedStep[]): string[] {
    const names = new Set<string>();
    for (const s of steps) {
        for (const m of (s.value ?? '').matchAll(/\{\{secret\.([A-Za-z0-9_]+)\}\}/g)) names.add(m[1]);
    }
    return [...names];
}

export function parseRecorderMessage(ev: MessageEvent, win: Window = window): RecorderMessage | null {
    if (ev.source !== win || ev.origin !== win.location.origin) return null;
    const d = ev.data as Record<string, unknown> | null;
    if (!d || d.source !== 'traceiq-recorder' || typeof d.type !== 'string') return null;
    switch (d.type) {
        case 'pong':
            return {
                type: 'pong', version: String(d.version ?? ''), trusted: d.trusted === true,
                recording: d.recording === true, pending: typeof d.pending === 'number' ? d.pending : 0,
            };
        case 'steps':
            return { type: 'steps', steps: cleanRecordedSteps(d.steps) };
        case 'error':
            return { type: 'error', error: text(d.error, 300) ?? 'The recorder reported an error.' };
        case 'starting': case 'needs-approval': case 'started': case 'denied': case 'ok':
            return { type: d.type };
        default:
            return null;
    }
}

export function askRecorder(type: 'ping' | 'start' | 'collect' | 'received' | 'cancel', extra: { url?: string } = {}) {
    window.postMessage({ source: 'traceiq-app', type, ...extra }, window.location.origin);
}

/** A typed address without a scheme gets https://; anything not http(s) is refused. */
export function startUrl(input: string): string | null {
    const trimmed = input.trim();
    if (!trimmed) return null;
    const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed
        : /^(localhost|127\.|10\.|192\.168\.)/i.test(trimmed) ? `http://${trimmed}` : `https://${trimmed}`;
    try {
        const u = new URL(candidate);
        return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null;
    } catch {
        return null;
    }
}

/** Set at build time once the extension is in the Chrome Web Store. */
export const RECORDER_STORE_URL: string = import.meta.env.VITE_RECORDER_STORE_URL || '';
export const RECORDER_ZIP_URL = '/recorder/traceiq-recorder.zip';
