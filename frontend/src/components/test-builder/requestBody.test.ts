import { describe, it, expect } from 'vitest';
import { bodyToText, textToBody } from './requestBody';

// http-request params.body is "object or string" (backend agent_reference.py);
// the worker hands it to Playwright as-is, so an object is sent as JSON.

describe('bodyToText', () => {
    it('shows an object body as JSON, not [object Object]', () => {
        expect(bodyToText({ sku: 'A1', qty: 2 })).toBe('{\n  "sku": "A1",\n  "qty": 2\n}');
        expect(bodyToText([1, 2])).toBe('[\n  1,\n  2\n]');
    });

    it('shows a string body as written, and nothing for no body', () => {
        expect(bodyToText('a=1&b=2')).toBe('a=1&b=2');
        expect(bodyToText(undefined)).toBe('');
        expect(bodyToText(null)).toBe('');
    });
});

describe('textToBody', () => {
    it('keeps an object body an object when the edit is valid JSON', () => {
        expect(textToBody('{"sku":"B2"}', { sku: 'A1' })).toEqual({ body: { sku: 'B2' } });
    });

    it('never replaces an object body with invalid text', () => {
        const previous = { sku: 'A1' };
        const r = textToBody('{"sku": "B2"', previous);
        expect(r.body).toBe(previous);
        expect(r.error).toMatch(/JSON/);
    });

    it('drops an object body when the editor is emptied', () => {
        expect(textToBody('   ', { sku: 'A1' })).toEqual({ body: undefined });
    });

    it('keeps a string body a string, even when it looks like JSON', () => {
        expect(textToBody('{"a":1}', 'old')).toEqual({ body: '{"a":1}' });
        expect(textToBody('plain text', undefined)).toEqual({ body: 'plain text' });
    });
});
