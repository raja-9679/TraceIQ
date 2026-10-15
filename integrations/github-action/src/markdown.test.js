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
