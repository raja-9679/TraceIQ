import { afterEach, describe, expect, it, vi } from 'vitest';
import { newId } from './ids';

describe('newId', () => {
    afterEach(() => vi.restoreAllMocks());

    it('returns a v4 UUID when randomUUID is unavailable (plain-http origin)', () => {
        vi.spyOn(globalThis, 'crypto', 'get').mockReturnValue({
            getRandomValues: (a: Uint8Array) => { a.fill(0xab); return a; },
        } as unknown as Crypto);
        expect(newId()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    });

    it('gives distinct ids', () => {
        expect(newId()).not.toEqual(newId());
    });
});
