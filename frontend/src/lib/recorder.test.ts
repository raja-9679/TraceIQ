import { describe, expect, it } from 'vitest';
import { cleanRecordedSteps, parseRecorderMessage, secretsNeeded, startUrl } from './recorder';

describe('cleanRecordedSteps', () => {
    it('keeps recordable steps, rebuilt with new ids and only known fields', () => {
        const out = cleanRecordedSteps([
            { id: 'x', type: 'click', selector: '#go', intent: 'Go', _frame: '#f', params: { evil: true } },
            { id: 'y', type: 'fill', selector: 'input[name="q"]', value: 'shoes' },
        ]);
        expect(out).toHaveLength(2);
        expect(out[0]).toEqual({ id: expect.any(String), type: 'click', selector: '#go', value: '', intent: 'Go' });
        expect(out[0].id).not.toBe('x');
        expect(out[1]).toEqual({ id: expect.any(String), type: 'fill', selector: 'input[name="q"]', value: 'shoes' });
    });

    it('drops step types the recorder never produces, and junk', () => {
        const out = cleanRecordedSteps([
            { type: 'run-script', value: 'fetch("/steal")' },
            { type: 'http-request', value: 'https://example.com' },
            null, 'click', { selector: '#a' }, { type: 7 },
            { type: 'expect-title', value: 'Home' },
        ]);
        expect(out.map((s) => s.type)).toEqual(['expect-title']);
    });

    it('caps the number of steps and the length of each field', () => {
        const many = Array.from({ length: 600 }, () => ({ type: 'click', selector: 'a'.repeat(5000) }));
        const out = cleanRecordedSteps(many);
        expect(out).toHaveLength(500);
        expect(out[0].selector).toHaveLength(1000);
    });

    it('returns nothing for a non-list', () => {
        expect(cleanRecordedSteps({ type: 'click' })).toEqual([]);
        expect(cleanRecordedSteps(undefined)).toEqual([]);
    });
});

describe('secretsNeeded', () => {
    it('lists each secret name once', () => {
        expect(secretsNeeded(cleanRecordedSteps([
            { type: 'fill', selector: '#p', value: '{{secret.PASSWORD}}' },
            { type: 'fill', selector: '#p2', value: '{{secret.PASSWORD}}' },
            { type: 'fill', selector: '#pin', value: '{{secret.PIN_CODE}}' },
            { type: 'fill', selector: '#u', value: 'alice' },
        ]))).toEqual(['PASSWORD', 'PIN_CODE']);
    });
});

describe('parseRecorderMessage', () => {
    const win = { location: { origin: 'https://tiq.example' } } as unknown as Window;
    const ev = (data: unknown, over: Partial<{ source: unknown; origin: string }> = {}) =>
        ({ data, source: win, origin: 'https://tiq.example', ...over }) as unknown as MessageEvent;

    it('reads a pong', () => {
        expect(parseRecorderMessage(ev({ source: 'traceiq-recorder', type: 'pong', version: '0.2.0', trusted: true, pending: 3 }), win))
            .toEqual({ type: 'pong', version: '0.2.0', trusted: true, recording: false, pending: 3 });
    });

    it('cleans the steps it carries', () => {
        const msg = parseRecorderMessage(ev({ source: 'traceiq-recorder', type: 'steps', steps: [{ type: 'click', selector: '#a' }, { type: 'run-script' }] }), win);
        expect(msg?.type === 'steps' && msg.steps.map((s) => s.type)).toEqual(['click']);
    });

    it('ignores other windows, other origins, other senders and unknown types', () => {
        const good = { source: 'traceiq-recorder', type: 'ok' };
        expect(parseRecorderMessage(ev(good), win)).toEqual({ type: 'ok' });
        expect(parseRecorderMessage(ev(good, { source: {} }), win)).toBeNull();
        expect(parseRecorderMessage(ev(good, { origin: 'https://evil.example' }), win)).toBeNull();
        expect(parseRecorderMessage(ev({ source: 'traceiq-app', type: 'ok' }), win)).toBeNull();
        expect(parseRecorderMessage(ev({ source: 'traceiq-recorder', type: 'surprise' }), win)).toBeNull();
        expect(parseRecorderMessage(ev(null), win)).toBeNull();
    });
});

describe('startUrl', () => {
    it('adds a scheme when none is typed', () => {
        expect(startUrl('shop.example.com/login')).toBe('https://shop.example.com/login');
        expect(startUrl('localhost:3000')).toBe('http://localhost:3000/');
        expect(startUrl(' http://10.0.0.5:8080/app ')).toBe('http://10.0.0.5:8080/app');
    });

    it('refuses anything that is not a web address', () => {
        expect(startUrl('')).toBeNull();
        expect(startUrl('javascript://alert(1)')).toBeNull();
        expect(startUrl('file:///etc/passwd')).toBeNull();
        expect(startUrl('chrome://extensions')).toBeNull();
    });
});
