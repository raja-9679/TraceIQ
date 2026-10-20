import { test } from 'node:test';
import assert from 'node:assert/strict';
import { legacyEngineBanner } from './legacy-engine';

test('the boot banner names every control the legacy engine skips', () => {
    const banner = legacyEngineBanner();
    assert.match(banner, /DEPRECATED/);
    for (const control of ['ai_allowed', 'egress', 'cancel']) assert.ok(banner.includes(control), control);
    assert.match(banner, /REMOVED/);
});
