/**
 * The `totp` step: generates a 2FA code from a {{secret.X}} seed and stores
 * it as a test variable that later steps (fill, http-request) interpolate.
 */
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { TestExecutor } from './test-executor';
import { totp, totpClock } from './totp';

const SEED = 'JBSWY3DPEHPK3PXP';
const SETTINGS = { secrets: { MFA_SEED: SEED } };

class Recorder {
    public filled: string[] = [];
    locator() {
        const rec = this;
        return { first() { return this; }, async waitFor() {}, async fill(v: string) { rec.filled.push(v); } };
    }
    isClosed() { return false; }
}

const realClock = { ...totpClock };
afterEach(() => Object.assign(totpClock, realClock));

function freezeAt(t: number) {
    const slept: number[] = [];
    let now = t;
    totpClock.now = () => now;
    totpClock.sleep = async (ms: number) => { slept.push(ms); now += ms / 1000; };
    return slept;
}

async function run(steps: any[], rec = new Recorder()) {
    const ctx: any = { variables: {} };
    for (const step of steps) await TestExecutor.executeStep(rec as any, rec as any, step, SETTINGS, ctx);
    return { rec, ctx };
}

test('stores the current code under the variable name and fill uses it', async () => {
    freezeAt(1700000000);
    const { rec, ctx } = await run([
        { type: 'totp', params: { secret: '{{secret.MFA_SEED}}', variableName: 'otp' } },
        { type: 'fill', selector: '#code', value: '{{otp}}' },
    ]);
    assert.equal(ctx.variables.otp, '324550');
    assert.deepEqual(rec.filled, ['324550']);
});

test('defaults the variable name to "otp"', async () => {
    freezeAt(1700000000);
    const { ctx } = await run([{ type: 'totp', params: { secret: '{{secret.MFA_SEED}}' } }]);
    assert.equal(ctx.variables.otp, totp(SEED, { at: 1700000000 }));
});

test('waits for the next window when the code is about to expire', async () => {
    const slept = freezeAt(1700000037); // 3 s left in this window
    const { ctx } = await run([{ type: 'totp', params: { secret: '{{secret.MFA_SEED}}' } }]);
    assert.deepEqual(slept, [3000]);
    assert.equal(ctx.variables.otp, totp(SEED, { at: 1700000040 }));
});

test('refuses a pasted seed instead of a project secret', async () => {
    freezeAt(1700000000);
    await assert.rejects(
        run([{ type: 'totp', params: { secret: SEED } }]),
        /project secret.*\{\{secret\.NAME\}\}/,
    );
});

test('fails clearly when the referenced secret does not exist', async () => {
    freezeAt(1700000000);
    await assert.rejects(
        run([{ type: 'totp', params: { secret: '{{secret.NOPE}}' } }]),
        /secret|base32/i,
    );
});

test('is listed as a supported step type', () => {
    assert.ok(TestExecutor.SUPPORTED_STEP_TYPES.includes('totp'));
});
