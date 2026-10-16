import { test } from 'node:test';
import assert from 'node:assert/strict';

import { _resetAiGate, aiAllowedForRun, jobAllowsAi, withAiGate } from './ai-gate';
import { _setProviderForTests, provider } from '../llm-provider';
import { AIEngine } from '../ai';

function fakeProvider() {
    const calls: string[] = [];
    return {
        calls,
        p: {
            name: 'fake',
            async complete(prompt: string) {
                calls.push(prompt);
                return '#healed';
            },
        },
    };
}

test('only an explicit ai_allowed=false blocks (older backends send nothing)', () => {
    assert.equal(jobAllowsAi(undefined), true);
    assert.equal(jobAllowsAi({}), true);
    assert.equal(jobAllowsAi({ ai_allowed: true }), true);
    assert.equal(jobAllowsAi({ ai_allowed: 'false' }), true);
    assert.equal(jobAllowsAi({ ai_allowed: false }), false);
});

test('ai_allowed=false: the provider is never called for that run, heal included', async () => {
    _resetAiGate();
    const fake = fakeProvider();
    const prev = _setProviderForTests(fake.p);
    try {
        const engine = new AIEngine();
        await withAiGate(7, { ai_allowed: false }, async () => {
            assert.equal(aiAllowedForRun(7), false);
            assert.equal(await provider.complete('analyze', { runId: 7, feature: 'failure_analysis' }), '');
            assert.equal(await engine.healSelector('#gone', '<html><button id="x"/></html>', 7), '');
            assert.equal(await engine.healMobileLocator('~gone', '<hierarchy/>', 7), '');
            // Another run in the same process is unaffected.
            assert.equal(await provider.complete('other', { runId: 8 }), '#healed');
        });
        assert.deepEqual(fake.calls, ['other']);
        // The block ends with the job.
        assert.equal(aiAllowedForRun(7), true);
        assert.equal(await provider.complete('after', { runId: 7 }), '#healed');
    } finally {
        _setProviderForTests(prev);
        _resetAiGate();
    }
});

test('ai_allowed true or absent leaves LLM calls alone', async () => {
    _resetAiGate();
    const fake = fakeProvider();
    const prev = _setProviderForTests(fake.p);
    try {
        await withAiGate(9, { ai_allowed: true }, async () => {
            assert.equal(await provider.complete('a', { runId: 9 }), '#healed');
        });
        await withAiGate(9, {}, async () => {
            assert.equal(await provider.complete('b', { runId: 9 }), '#healed');
        });
        assert.equal(fake.calls.length, 2);
    } finally {
        _setProviderForTests(prev);
    }
});

test('overlapping jobs of one run keep the block until the last one ends', async () => {
    _resetAiGate();
    let releaseFirst!: () => void;
    const first = withAiGate(3, { ai_allowed: false }, () => new Promise<void>((r) => { releaseFirst = r; }));
    await withAiGate(3, { ai_allowed: false }, async () => undefined);
    assert.equal(aiAllowedForRun(3), false);
    releaseFirst();
    await first;
    assert.equal(aiAllowedForRun(3), true);
});

test('the block is released when the job throws', async () => {
    _resetAiGate();
    await assert.rejects(withAiGate(4, { ai_allowed: false }, async () => { throw new Error('boom'); }));
    assert.equal(aiAllowedForRun(4), true);
});
