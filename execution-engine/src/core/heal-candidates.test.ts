/**
 * W5 heal candidates: each deterministic strategy, the scoring/ranking, and
 * the runtime heal applying only a uniquely matching top candidate (fake page,
 * fake LLM). Mirrors backend/tests/test_heal_candidates.py.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTree, walk, HtmlElement } from './html-lite';
import {
    accessibleName, classifySelector, cssPathStrategy, deterministicCandidates, findTargets,
    idStrategy, labelStrategy, looksGenerated, rankHealCandidates, roleStrategy, scoreCandidate,
    similarity, STABILITY, testIdStrategy, textStrategy, tokens, WEIGHTS,
} from './heal-candidates';
import { HealPage, runtimeHeal } from './selector-heal';

const PAGE = `<html><body>
  <form id="login-form" class="auth card">
    <label for="email">Email address</label>
    <input id="email" name="email" type="email" placeholder="you@example.com">
    <input id="pw" name="password" type="password" value="hunter2">
    <button data-testid="login-submit" class="btn btn-primary" type="submit">Sign in</button>
    <a href="/forgot?token=abc">Forgot password?</a>
  </form>
  <div class="footer"><span>Sign in</span><span>Help</span></div>
</body></html>`;

function el(html: string, pred: (e: HtmlElement) => boolean): { root: HtmlElement; e: HtmlElement } {
    const root = buildTree(html);
    for (const e of walk(root)) if (pred(e)) return { root, e };
    throw new Error('element not found');
}

test('tokens split camelCase and punctuation and drop selector noise', () => {
    assert.deepEqual(tokens('#loginSubmit-btn'), ['login', 'submit', 'btn']);
    assert.deepEqual(tokens('div > span.nth-child'), []);
});

test('similarity rewards exact and prefix token matches', () => {
    assert.equal(similarity(['submit'], ['submit']), 1);
    assert.equal(similarity(['submit'], ['submitting']), 0.8);
    assert.equal(similarity(['submit', 'login'], ['login']), 0.5);
    assert.equal(similarity([], ['x']), 0);
});

test('test-id strategy emits one selector per test-id attribute', () => {
    const { e } = el('<button data-testid="save" data-qa="save-qa">Save</button>', x => x.tag === 'button');
    assert.deepEqual(testIdStrategy(e), ['[data-testid="save"]', '[data-qa="save-qa"]']);
});

test('id strategy: css form for plain ids, attribute form otherwise, flags generated ids', () => {
    assert.deepEqual(idStrategy(el('<i id="save"></i>', x => x.tag === 'i').e), [{ selector: '#save', generated: false }]);
    assert.deepEqual(idStrategy(el('<i id="a b"></i>', x => x.tag === 'i').e), [{ selector: '[id="a b"]', generated: false }]);
    assert.equal(idStrategy(el('<i id="ember1234"></i>', x => x.tag === 'i').e)[0].generated, true);
    assert.ok(looksGenerated(':r1:') && looksGenerated('x-9f8e7d6c5b') && !looksGenerated('email'));
});

test('role strategy uses implicit roles and the accessible name', () => {
    const { root, e } = el(PAGE, x => x.tag === 'button');
    assert.deepEqual(roleStrategy(e, root), ['role=button[name="Sign in"]']);
    const email = el(PAGE, x => x.attrs.get('id') === 'email');
    assert.equal(accessibleName(email.e, email.root), 'Email address');
    assert.deepEqual(roleStrategy(email.e, email.root), ['role=textbox[name="Email address"]']);
    // A div has no role, so no candidate.
    assert.deepEqual(roleStrategy(el('<div>Hi</div>', x => x.tag === 'div').e, buildTree('')), []);
});

test('label strategy emits aria-label, placeholder and name forms', () => {
    const { e } = el('<input aria-label="Search" placeholder="Find…" name="q">', x => x.tag === 'input');
    assert.deepEqual(labelStrategy(e), ['[aria-label="Search"]', '[placeholder="Find…"]', 'input[name="q"]']);
});

test('text strategy picks the innermost element and skips long or redacted text', () => {
    assert.deepEqual(textStrategy(el('<span>Sign in</span>', x => x.tag === 'span').e), ['text="Sign in"']);
    assert.deepEqual(textStrategy(el('<p><b>Sign in</b></p>', x => x.tag === 'p').e), []);
    assert.deepEqual(textStrategy(el(`<p>${'x'.repeat(60)}</p>`, x => x.tag === 'p').e), []);
    assert.deepEqual(textStrategy(el('<p>[REDACTED:pan]</p>', x => x.tag === 'p').e), []);
});

test('css path stops at a stable id and never uses nth-child', () => {
    const { e } = el(PAGE, x => x.tag === 'button');
    const [path] = cssPathStrategy(e);
    assert.equal(path, 'form#login-form > button.btn.btn-primary[type="submit"]');
    assert.ok(!path.includes('nth'));
});

test('selector escaping survives quotes in values', () => {
    const { e } = el('<button aria-label=\'Say "hi"\'>x</button>', x => x.tag === 'button');
    assert.deepEqual(labelStrategy(e), ['[aria-label="Say \\"hi\\""]']);
});

test('targets are the elements most like the old selector and intent', () => {
    const root = buildTree(PAGE);
    const targets = findTargets(root, [...tokens('#login-btn'), ...tokens('submit the login form')]);
    assert.equal(targets[0].el.tag, 'button');
});

test('deterministic candidates cover every strategy for the best target', () => {
    const drafts = deterministicCandidates(PAGE, '#login-btn', 'submit the login form');
    const strategies = new Set(drafts.filter(d => d.target === 0).map(d => d.strategy));
    for (const s of ['test-id', 'role', 'text', 'css-path']) assert.ok(strategies.has(s as any), s);
});

test('classifySelector orders forms by stability', () => {
    assert.equal(classifySelector('[data-testid="x"]'), STABILITY['test-id']);
    assert.equal(classifySelector('#save'), STABILITY.id);
    assert.equal(classifySelector('role=button[name="Go"]'), STABILITY.role);
    assert.equal(classifySelector('button:has-text("Go")'), STABILITY.text);
    assert.equal(classifySelector('div > div > span'), 0.4);
});

test('score is the weighted sum of the breakdown', () => {
    const b = { stability: 1, similarity: 0.5, llm: 0.6, uniqueness: 1 };
    assert.equal(scoreCandidate(b), Math.round((WEIGHTS.stability + 0.5 * WEIGHTS.similarity + 0.6 * WEIGHTS.llm + WEIGHTS.uniqueness) * 1000) / 1000);
});

/** A fake page whose match counts come from a table. */
function fakePage(html: string, counts: Record<string, number>, same: Record<string, boolean> = {}): HealPage {
    return {
        content: async () => html,
        isClosed: () => false,
        locator: (sel: string) => ({
            count: async () => counts[sel] ?? 0,
            elementHandle: async () => ({ sel }),
            evaluate: async (_fn: any, other: any) => !!same[`${sel}|${other.sel}`],
        }),
    };
}

test('ranking puts unique candidates first and records evidence for all', async () => {
    const ranked = await rankHealCandidates({
        html: PAGE, oldSelector: '#login-btn', intent: 'submit the login form',
        llmSelector: 'button.btn', llmConfidence: 0.9,
        count: async sel => ({ '[data-testid="login-submit"]': 1, 'text="Sign in"': 2, 'button.btn': 1 } as any)[sel] ?? 0,
    });
    assert.equal(ranked[0].selector, '[data-testid="login-submit"]');
    assert.equal(ranked[0].applicable, true);
    const text = ranked.find(c => c.selector === 'text="Sign in"')!;
    assert.equal(text.applicable, false);
    assert.equal(text.matches, 2);
    const llm = ranked.find(c => c.selector === 'button.btn')!;
    assert.equal(llm.source, 'llm');
    assert.equal(llm.breakdown.llm, 0.9);
    // Every applicable candidate ranks ahead of every non-applicable one.
    const firstNA = ranked.findIndex(c => !c.applicable);
    assert.ok(ranked.slice(firstNA).every(c => !c.applicable));
});

test('a deterministic candidate on the LLM\'s element earns the LLM component', async () => {
    const ranked = await rankHealCandidates({
        html: PAGE, oldSelector: '#login-btn', intent: 'submit', llmSelector: 'form button', llmConfidence: 0.8,
        count: async sel => (sel === 'form button' || sel === '[data-testid="login-submit"]' ? 1 : 0),
        sameElement: async (a, b) => a === '[data-testid="login-submit"]' && b === 'form button',
    });
    assert.equal(ranked.find(c => c.selector === '[data-testid="login-submit"]')!.breakdown.llm, 0.8);
});

test('runtime heal applies the top unique candidate, not the LLM pick', async () => {
    const page = fakePage(PAGE, { '[data-testid="login-submit"]': 1, 'button.btn': 1, 'text="Sign in"': 2 });
    const tried: string[] = [];
    let llmSawDom = '';
    const out = await runtimeHeal({
        page, step: { id: 's1', selector: '#login-btn', intent: 'submit the login form' }, testCaseId: 7,
        job: {}, llm: async (_sel, dom) => { llmSawDom = dom; return 'button.btn'; },
        retry: async sel => { tried.push(sel); },
    });
    assert.equal(out.recovered, true);
    assert.deepEqual(tried, ['[data-testid="login-submit"]']);
    assert.equal(out.suggestion!.applied, true);
    assert.equal(out.suggestion!.new_selector, '[data-testid="login-submit"]');
    assert.ok(out.suggestion!.candidates.length >= 3);
    // The LLM and the snapshot only ever see the redacted DOM.
    for (const dom of [llmSawDom, out.suggestion!.dom_snapshot]) {
        assert.ok(!dom.includes('hunter2'));
        assert.ok(!dom.includes('token=abc'));
        assert.ok(dom.includes('data-testid="login-submit"'));
    }
});

test('runtime heal does not apply when no candidate is unique, but still records', async () => {
    const page = fakePage(PAGE, { 'text="Sign in"': 2, 'button.btn': 3 });
    const tried: string[] = [];
    const out = await runtimeHeal({
        page, step: { id: 's1', selector: '#login-btn' }, testCaseId: 7, job: {},
        llm: async () => 'button.btn', retry: async sel => { tried.push(sel); },
    });
    assert.equal(out.recovered, false);
    assert.deepEqual(tried, []);
    assert.equal(out.suggestion!.applied, false);
    assert.ok(out.suggestion!.matches > 1);
});

test('runtime heal works without an LLM (deterministic candidates only)', async () => {
    const page = fakePage(PAGE, { '[data-testid="login-submit"]': 1 });
    const out = await runtimeHeal({
        page, step: { id: 's1', selector: '#login-btn', intent: 'submit' }, testCaseId: 7, job: {},
        llm: null, retry: async () => {},
    });
    assert.equal(out.recovered, true);
    assert.ok(out.suggestion!.candidates.every(c => c.source === 'deterministic'));
});

test('runtime heal applies the job mask selectors before ranking', async () => {
    const html = '<div class="pii"><button data-testid="acct">Jane Doe</button></div>';
    let seen = '';
    await runtimeHeal({
        page: fakePage(html, {}), step: { id: 's', selector: '#acct' }, testCaseId: 1,
        job: { data_policy: { mask_selectors: ['.pii'] } },
        llm: async (_s, dom) => { seen = dom; return ''; }, retry: async () => {},
    });
    assert.ok(!seen.includes('Jane Doe'));
    assert.ok(seen.includes('data-testid="acct"'));
});
