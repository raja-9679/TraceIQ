const test = require('node:test');
const assert = require('node:assert/strict');
const { gateSection, mdCell } = require('./markdown');

test('renders every check the server sends, including new ones, with detail', () => {
    const out = gateSection({
        passed: false,
        checks: [
            { name: 'pass_rate', passed: true, actual: '100%', threshold: '>= 100%' },
            { name: 'critical_passed', passed: false, missing_data: true, actual: '1 of 2 passed',
              threshold: 'all critical passed', detail: 'no result for commit abc: pay' },
            { name: 'flaky_cases', passed: false, actual: '3 flaky', threshold: '<= 1', detail: 'a, b | c' },
        ],
    }, true).join('\n');
    assert.match(out, /\| pass_rate \| :white_check_mark: \| 100% \| >= 100% \|  \|/);
    assert.match(out, /\| critical_passed \| :grey_question: no data \| 1 of 2 passed \| all critical passed \| no result for commit abc: pay \|/);
    assert.match(out, /\| flaky_cases \| :x: \| 3 flaky \| <= 1 \| a, b \\\| c \|/);
});

test('no gate renders nothing', () => {
    assert.deepEqual(gateSection(null, true), []);
});

test('mdCell escapes pipes and truncates', () => {
    assert.equal(mdCell('a|b'), 'a\\|b');
    assert.equal(mdCell('x'.repeat(10), 5), 'xxxx…');
});

const { failuresSection, mdCode } = require('./markdown');

test('failing cases list the failing step, truncated error, expiring screenshot and run link', () => {
    const out = failuresSection([{ runId: 42, failures: [
        { test_name: 'checkout | pay', status: 'failed', error: 'x'.repeat(1000),
          failing_step: { index: 2, type: 'click', selector: "button:has-text('Pay')", error: 'Timeout 30000ms exceeded' },
          screenshot_url: 'https://minio/runs/42/s.png?X-Amz-Expires=3600', screenshot_expires_in: 3600 },
        { test_name: 'login', status: 'error', error: 'boom\nline2', failing_step: null, screenshot_url: null },
    ] }], 'https://tiq.example');
    const text = out.join('\n');
    assert.match(text, /### Failing cases/);
    assert.match(text, /\*\*checkout \\\| pay\*\* — step 3 `click` on `button:has-text\('Pay'\)`/);
    assert.match(text, /> Timeout 30000ms exceeded/);           // the step error wins over the case error
    assert.match(text, /\[Screenshot\]\(https:\/\/minio\/runs\/42\/s\.png\?X-Amz-Expires=3600\) _\(link expires in 1h\)_/);
    assert.match(text, /\[Run #42\]\(https:\/\/tiq\.example\/runs\/42\)/);
    assert.match(text, /\*\*login\*\* — no step evidence/);
    assert.match(text, /> boom line2/);
    assert.ok(!text.includes('x'.repeat(301)));
});

test('no failures renders nothing and long lists are capped', () => {
    assert.deepEqual(failuresSection([], 'u'), []);
    assert.deepEqual(failuresSection(undefined, 'u'), []);
    const many = Array.from({ length: 25 }, (_, i) => ({ test_name: `c${i}`, error: 'e' }));
    const text = failuresSection([{ runId: 1, failures: many }], 'u').join('\n');
    assert.match(text, /and 5 more failing case/);
});

test('code spans survive backticks in selectors', () => {
    assert.equal(mdCode('a`b'), '``a`b``');
    assert.equal(mdCode('`x'), '`` `x ``');
    assert.equal(mdCode(''), '');
});
