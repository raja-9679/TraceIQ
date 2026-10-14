import { createHmac } from 'crypto';

/**
 * RFC 6238 TOTP (HMAC-SHA1), the scheme Google/Microsoft Authenticator and
 * most sites' 2FA use. Mirrors backend app/core/totp.py; totp.test.ts pins
 * both to the RFC vectors and to each other.
 */

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

/** Decode a base32 seed as authenticator apps display it: case, spaces and padding are ignored. */
export function decodeBase32(seed: string): Buffer {
    const clean = seed.replace(/[\s=-]/g, '').toUpperCase();
    if (!clean) throw new Error('TOTP secret is empty');
    let bits = 0;
    let value = 0;
    const out: number[] = [];
    for (const ch of clean) {
        const idx = ALPHABET.indexOf(ch);
        if (idx < 0) throw new Error('TOTP secret is not valid base32 (letters A-Z and digits 2-7)');
        value = (value << 5) | idx;
        bits += 5;
        if (bits >= 8) {
            bits -= 8;
            out.push((value >>> bits) & 0xff);
        }
    }
    return Buffer.from(out);
}

export interface TotpOptions {
    /** Unix time in seconds; defaults to now. */
    at?: number;
    digits?: number;
    period?: number;
}

export function totp(seed: string, { at = Date.now() / 1000, digits = 6, period = 30 }: TotpOptions = {}): string {
    const counter = Buffer.alloc(8);
    counter.writeBigUInt64BE(BigInt(Math.floor(at / period)));
    const mac = createHmac('sha1', decodeBase32(seed)).update(counter).digest();
    const offset = mac[mac.length - 1] & 0x0f;
    const binary = (mac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits;
    return String(binary).padStart(digits, '0');
}

/** Seconds until the code for `at` stops being valid. */
export function secondsLeft(at: number, period = 30): number {
    return period - (Math.floor(at) % period);
}

/**
 * True when the step's secret field holds a pasted key rather than a
 * `{{secret.NAME}}` reference. Steps are stored and audited in plaintext;
 * only project secrets are encrypted, so a raw seed is refused.
 */
export function looksLikeLiteralSeed(raw: string): boolean {
    return !/^\s*\{\{\s*secret\.[A-Za-z0-9_.-]+\s*\}\}\s*$/.test(raw);
}

/** Time source for the step's expiry guard; tests replace it. */
export const totpClock = {
    now: (): number => Date.now() / 1000,
    sleep: (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms)),
};
