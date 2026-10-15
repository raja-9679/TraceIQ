import { test } from 'node:test';
import assert from 'node:assert/strict';

import { _resetWorkerConfigState, applyWorkerEnv } from './worker-config';
import { envFromLlmConfig, pickProvider } from '../llm-provider';

test('delivered values override env; null restores the worker\'s own env', () => {
    _resetWorkerConfigState();
    const env: any = { DEFAULT_TIMEOUT: '30000', RUNTIME_HEAL_ENABLED: 'false' };
    let changed = applyWorkerEnv({ env: { DEFAULT_TIMEOUT: '45000', RUNTIME_HEAL_ENABLED: 'true', MAX_JOB_DURATION_MS: null, HAR_CAPTURE_ENABLED: null } }, env);
    assert.equal(env.DEFAULT_TIMEOUT, '45000');
    assert.equal(env.RUNTIME_HEAL_ENABLED, 'true');
    assert.equal(env.MAX_JOB_DURATION_MS, undefined);
    assert.deepEqual(changed.sort(), ['DEFAULT_TIMEOUT', 'RUNTIME_HEAL_ENABLED']);

    // The backend stops managing them: the ORIGINAL env comes back, not the
    // last delivered value.
    changed = applyWorkerEnv({ env: { DEFAULT_TIMEOUT: null, RUNTIME_HEAL_ENABLED: null } }, env);
    assert.equal(env.DEFAULT_TIMEOUT, '30000');
    assert.equal(env.RUNTIME_HEAL_ENABLED, 'false');
    assert.deepEqual(changed.sort(), ['DEFAULT_TIMEOUT', 'RUNTIME_HEAL_ENABLED']);
});

test('a variable absent from the env is removed again when unmanaged', () => {
    _resetWorkerConfigState();
    const env: any = {};
    applyWorkerEnv({ env: { HAR_CAPTURE_ENABLED: 'true' } }, env);
    assert.equal(env.HAR_CAPTURE_ENABLED, 'true');
    applyWorkerEnv({ env: {} }, env);
    assert.equal('HAR_CAPTURE_ENABLED' in env, false);
});

test('delivered LLM config maps onto the provider-specific env names', () => {
    assert.deepEqual(envFromLlmConfig({ provider: 'anthropic', model: 'm', api_key: 'k' }),
        { LLM_PROVIDER: 'anthropic', LLM_MODEL: 'm', ANTHROPIC_API_KEY: 'k' });
    assert.deepEqual(envFromLlmConfig({ provider: 'openai-compatible', model: 'm', api_key: 'k', base_url: 'http://x/v1' }),
        { LLM_PROVIDER: 'openai-compatible', LLM_MODEL: 'm', LLM_API_KEY: 'k', LLM_BASE_URL: 'http://x/v1' });
    assert.deepEqual(envFromLlmConfig({ provider: 'ollama', base_url: 'http://o:11434/v1' }),
        { LLM_PROVIDER: 'ollama', OLLAMA_BASE_URL: 'http://o:11434/v1' });
});

test('provider "null" disables worker-side AI even with keys in the env', () => {
    assert.equal(pickProvider({ LLM_PROVIDER: 'null', ANTHROPIC_API_KEY: 'k' } as any).name, 'null');
    assert.equal(pickProvider({ LLM_PROVIDER: 'anthropic' } as any).name, 'null');   // no key
});
