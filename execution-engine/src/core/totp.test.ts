import { test } from 'node:test';
import assert from 'node:assert/strict';
import { totp, secondsLeft, decodeBase32, looksLikeLiteralSeed } from './totp';

// RFC 6238 Appendix B, SHA-1: seed "12345678901234567890" (ASCII), 8 digits.
const RFC_SEED = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';
const RFC_VECTORS: [number, string][] = [
    [59, '94287082'],
    [1111111109, '07081804'],
    [1111111111, '14050471'],
    [1234567890, '89005924'],
    [2000000000, '69279037'],
    [20000000000, '65353130'],
];

test('matches the RFC 6238 SHA-1 test vectors', () => {
    for (const [t, code] of RFC_VECTORS) {
        assert.equal(totp(RFC_SEED, { at: t, digits: 8 }), code, `t=${t}`);
    }
});

test('agrees with the backend implementation (app/core/totp.py)', () => {
    // Values produced by backend now_code(secret, t) — the two must not drift.
    assert.equal(totp('JBSWY3DPEHPK3PXP', { at: 1700000000 }), '324550');
    assert.equal(totp('JBSWY3DPEHPK3PXP', { at: 1700000029 }), '367665');
    assert.equal(totp(RFC_SEED, { at: 59 }), '287082');
});

test('accepts seeds the way authenticator apps show them', () => {
    const canonical = totp('JBSWY3DPEHPK3PXP', { at: 1700000000 });
    assert.equal(totp('jbsw y3dp ehpk 3pxp', { at: 1700000000 }), canonical);
    assert.equal(totp('JBSWY3DPEHPK3PXP====', { at: 1700000000 }), canonical);
});

test('honours a custom period', () => {
    // 60 s windows: t=59 and t=0 share a code, t=60 does not.
    assert.equal(totp(RFC_SEED, { at: 59, period: 60 }), totp(RFC_SEED, { at: 0, period: 60 }));
    assert.notEqual(totp(RFC_SEED, { at: 60, period: 60 }), totp(RFC_SEED, { at: 59, period: 60 }));
});

test('secondsLeft counts down to the end of the window', () => {
    assert.equal(secondsLeft(1700000010, 30), 30);
    assert.equal(secondsLeft(1700000037, 30), 3);
});

test('rejects a seed that is not base32', () => {
    assert.throws(() => decodeBase32('not-base32!'), /base32/);
    assert.throws(() => decodeBase32(''), /empty/);
});

test('recognises a pasted key versus a secret reference', () => {
    assert.equal(looksLikeLiteralSeed('JBSWY3DPEHPK3PXP'), true);
    assert.equal(looksLikeLiteralSeed('{{secret.MFA_SEED}}'), false);
    assert.equal(looksLikeLiteralSeed('  {{ secret.MFA_SEED }} '), false);
    assert.equal(looksLikeLiteralSeed('{{env.SEED}}'), true); // env vars are not encrypted
});
