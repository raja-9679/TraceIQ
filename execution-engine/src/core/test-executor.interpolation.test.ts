/**
 * Template interpolation must reach every step handler that accepts a
 * user-authored string.
 *
 * The `fill` regression this guards against was a credential-disclosure bug:
 * when `fill` did not resolve templates, `{{secret.PASSWORD}}` was typed into
 * the page literally, so users put real passwords in TestCase.steps — an
 * unencrypted JSON column whose contents are copied verbatim into
 * AuditLog.changes and every TestCaseRevision.snapshot.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TestExecutor } from './test-executor';

/** Records what actually reached the Playwright boundary. */
class FakeLocator {
    public filled: string[] = [];
    public selected: any[] = [];
    constructor(private readonly recorder: Recorder, private readonly text: string = '') {}
    first() { return this; }
    async waitFor() {}
    async hover() {}
    async click() {}
    async check() {}
    async fill(value: string) { this.recorder.filled.push(value); }
    async selectOption(value: any) { this.recorder.selected.push(value); }
    async textContent() { return this.text; }
}

class Recorder {
    public filled: string[] = [];
    public selected: any[] = [];
    public locatorSelectors: string[] = [];
    public elementText = '';

    locator(selector: string) {
        this.locatorSelectors.push(selector);
        const loc = new FakeLocator(this, this.elementText);
        return loc;
    }
    async waitForURL(url: string) { this.navigatedTo.push(url); }
    public navigatedTo: string[] = [];
    isClosed() { return false; }
    async title() { return this.elementText; }
}

const SETTINGS = {
    secrets: { PASSWORD: 'hunter2-real-secret', 'API-KEY': 'tiq_live_abc' },
    environment: { variables: { REGION: 'in-south' } },
};
const CASE_CTX = { data: { plan: 'gold' }, variables: { orderId: 'ORD-77' } };

async function run(step: any, recorder: Recorder) {
    return TestExecutor.executeStep(recorder as any, recorder as any, step, SETTINGS, CASE_CTX);
}

test('fill resolves {{secret.KEY}} before typing into the page', async () => {
    const rec = new Recorder();
    await run({ type: 'fill', selector: '#password', value: '{{secret.PASSWORD}}' }, rec);
    assert.deepEqual(rec.filled, ['hunter2-real-secret']);
});

test('fill resolves hyphenated secret keys', async () => {
    const rec = new Recorder();
    await run({ type: 'fill', selector: '#key', value: '{{secret.API-KEY}}' }, rec);
    assert.deepEqual(rec.filled, ['tiq_live_abc']);
});

test('fill resolves {{data.KEY}} from the dataset row', async () => {
    const rec = new Recorder();
    await run({ type: 'fill', selector: '#plan', value: '{{data.plan}}' }, rec);
    assert.deepEqual(rec.filled, ['gold']);
});

test('fill resolves templates embedded in a larger string', async () => {
    const rec = new Recorder();
    await run({ type: 'fill', selector: '#note', value: 'order {{orderId}} in {{env.REGION}}' }, rec);
    assert.deepEqual(rec.filled, ['order ORD-77 in in-south']);
});

test('fill leaves an unknown token in place so the mistake is visible', async () => {
    const rec = new Recorder();
    await run({ type: 'fill', selector: '#x', value: '{{secret.NOPE}}' }, rec);
    assert.deepEqual(rec.filled, ['{{secret.NOPE}}']);
});

test('select-option resolves templates before selecting', async () => {
    const rec = new Recorder();
    await run({ type: 'select-option', selector: '#plan', value: '{{data.plan}}' }, rec);
    assert.deepEqual(rec.selected, ['gold']);
});

test('expect-text resolves templates before comparing', async () => {
    const rec = new Recorder();
    rec.elementText = 'Your plan is gold';
    await run({ type: 'expect-text', selector: '#summary', value: '{{data.plan}}' }, rec);
    // Passing without throwing is the assertion: an unresolved '{{data.plan}}'
    // would not be found in the element text.
});

test('expect-url resolves templates before waiting', async () => {
    const rec = new Recorder();
    await run({ type: 'expect-url', value: '/orders/{{orderId}}' }, rec);
    assert.deepEqual(rec.navigatedTo, ['/orders/ORD-77']);
});

test('click resolves templates in the selector', async () => {
    const rec = new Recorder();
    await run({ type: 'click', selector: '[data-plan="{{data.plan}}"]' }, rec);
    assert.deepEqual(rec.locatorSelectors, ['[data-plan="gold"]']);
});
