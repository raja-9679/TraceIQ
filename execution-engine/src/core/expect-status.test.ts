/**
 * goto params.expect_status — a page that answers 404 must not pass a
 * "page opens" test just because the error page rendered.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseExpectedStatus, navigationStatusError } from './expect-status';
import { TestExecutor } from './test-executor';

function resp(status: number, url = 'https://site/a', from: string[] = []) {
    // Build the redirectedFrom() chain newest-first, as Playwright does.
    let prev: any = null;
    for (const u of from) {
        const p = prev;
        prev = { url: () => u, redirectedFrom: () => p };
    }
    const chainHead = prev;
    return { status: () => status, url: () => url, request: () => ({ redirectedFrom: () => chainHead }) };
}

test('parses single codes, lists, classes and arrays', () => {
    assert.equal(parseExpectedStatus(undefined), null);
    assert.equal(parseExpectedStatus(''), null);
    assert.equal(parseExpectedStatus(' , '), null);
    const one = parseExpectedStatus(200)!;
    assert.ok(one.matches(200) && !one.matches(404));
    const list = parseExpectedStatus('200, 301')!;
    assert.ok(list.matches(301) && !list.matches(302));
    const cls = parseExpectedStatus('2XX')!;
    assert.ok(cls.matches(204) && !cls.matches(301));
    const arr = parseExpectedStatus([404, '410'])!;
    assert.ok(arr.matches(410) && !arr.matches(200));
    assert.equal(arr.label, '404, 410');
});

test('rejects anything that is not a status code', () => {
    for (const bad of ['ok', '20', '600', '2x', '9xx', 'abc,200']) {
        assert.throws(() => parseExpectedStatus(bad), /not a status code/, bad);
    }
});

test('names status, final URL and redirect chain on a mismatch', () => {
    const m = parseExpectedStatus(200)!;
    assert.equal(navigationStatusError(resp(200) as any, m), null);
    const err = navigationStatusError(resp(404, 'https://site/b', ['https://site/a']) as any, m)!;
    assert.match(err, /Expected HTTP status 200, got 404 for https:\/\/site\/b \(redirected from https:\/\/site\/a\)/);
});

test('fails when there is no response to check', () => {
    assert.match(navigationStatusError(null, parseExpectedStatus(200)!)!, /no HTTP response/);
});

class FakePage {
    public gotoCalls = 0;
    constructor(private answer: any) {}
    isClosed() { return false; }
    async goto() { this.gotoCalls++; return this.answer; }
}

async function goto(page: FakePage, params: any) {
    const step = { type: 'goto', value: 'https://site/a', params };
    await TestExecutor.executeStep(page as any, page as any, step, {}, { variables: {} } as any);
}

test('goto passes on the expected status and fails, without retrying, on another', async () => {
    await goto(new FakePage(resp(200)), { expect_status: 200 });
    const page = new FakePage(resp(404));
    await assert.rejects(goto(page, { expect_status: '200' }), /got 404/);
    assert.equal(page.gotoCalls, 1);
});

test('goto can expect an error status (removed article)', async () => {
    await goto(new FakePage(resp(410)), { expect_status: '404,410' });
});

test('goto without expect_status keeps the old behaviour (404 page passes)', async () => {
    await goto(new FakePage(resp(404)), {});
    await goto(new FakePage(resp(404)), { expect_status: '' });
});

test('a malformed expect_status fails before navigating', async () => {
    const page = new FakePage(resp(200));
    await assert.rejects(goto(page, { expect_status: 'ok' }), /not a status code/);
    assert.equal(page.gotoCalls, 0);
});
