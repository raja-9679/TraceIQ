import { test } from 'node:test';
import assert from 'node:assert/strict';

import { resolveTemplates, templateContextFrom } from './interpolate';

test('flag tokens resolve from job settings.flags; booleans render as text', () => {
    const ctx = templateContextFrom({ flags: { checkout_v2: true, 'exp.price-test': 'B' } }, {});
    assert.equal(resolveTemplates('/cart?v2={{flag.checkout_v2}}', ctx), '/cart?v2=true');
    assert.equal(resolveTemplates('{{ flag.exp.price-test }}', ctx), 'B');
});

test('unknown flags and runs without flags leave the token visible', () => {
    const ctx = templateContextFrom({ flags: { a: 'x' } }, {});
    assert.equal(resolveTemplates('{{flag.missing}}', ctx), '{{flag.missing}}');
    assert.equal(resolveTemplates('{{flag.a}}', templateContextFrom({}, {})), '{{flag.a}}');
});
