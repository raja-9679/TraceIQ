/**
 * Shared twin vectors (WP15e, H8): the SAME input → expected-output files are
 * consumed here and by backend/tests/test_twin_vectors.py, so the TypeScript
 * and Python twins of the redaction and heal-ranking code are held to one
 * answer instead of two corpora that are supposed to agree.
 *
 * Files: tests/vectors/{redaction,heal_ranking}.json at the repo root.
 * Format and the rules for editing them: tests/vectors/README.md.
 *
 * A vector with `known_divergence` pins EACH twin's current output
 * (`expected_by_twin.typescript` here) so a change on either side is noticed;
 * it is a recorded disagreement, not an accepted one.
 *
 * `TWIN_VECTORS_ACTUAL=<file>` writes every actual output to that file — the
 * quickest way to see what this twin produces for a new vector.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'fs';
import * as path from 'path';
import {
    a11yHelpUrl, luhnValid, policyFromDataPolicy, queryNamesOnly, redactA11yTarget,
    redactAccessibilityResults, redactAccessObservation, redactBody, redactCrawlReport, redactDom,
    redactHar, redactHeaders, redactHookReport, redactJobResult, redactMessageText,
    redactNetworkEvents, redactPageBudgets, redactStepEvidence, redactText, verhoeffValid,
} from './redact';
import {
    accessibleName, classifySelector, deterministicCandidates, implicitRole, looksGenerated,
    rankHealCandidates, scoreCandidate, similarity, tokens,
} from './heal-candidates';
import { buildTree, walk } from './html-lite';

const VECTOR_DIR = path.resolve(__dirname, '../../../tests/vectors');

interface Vector {
    id: string;
    fn: string;
    args: Record<string, any>;
    data_policy?: any;
    expected?: any;
    expected_by_twin?: { python: any; typescript: any };
    known_divergence?: string;
    compare?: 'json';
}

/**
 * `{"$concat": [...]}` / `{"$repeat": [s, n]}` build strings the file must not
 * hold literally: vendor credential formats (secret scanners and push
 * protection would flag a contiguous token) and very long values.
 * `{"$fixture": "NAME"}` is the document's `fixtures.NAME` (a shared page).
 */
function resolve(x: any, fixtures: Record<string, any>): any {
    if (Array.isArray(x)) return x.map(i => resolve(i, fixtures));
    if (x && typeof x === 'object') {
        if (typeof x.$fixture === 'string') {
            assert.ok(x.$fixture in fixtures, `unknown fixture ${x.$fixture}`);
            return resolve(fixtures[x.$fixture], fixtures);
        }
        if (Array.isArray(x.$repeat)) return String(x.$repeat[0]).repeat(x.$repeat[1]);
        if (Array.isArray(x.$concat)) return x.$concat.map((i: any) => resolve(i, fixtures)).join('');
        return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, resolve(v, fixtures)]));
    }
    return x;
}

function load(file: string): Vector[] {
    const full = path.join(VECTOR_DIR, file);
    // Fail, never skip: a missing vector file must not read as "all twins agree".
    assert.ok(fs.existsSync(full), `twin vector file missing: ${full}`);
    const doc = JSON.parse(fs.readFileSync(full, 'utf8'));
    assert.equal(doc.format, 'traceiq-twin-vectors');
    return resolve(doc.vectors, doc.fixtures || {});
}

type Fn = (args: any, policy: any) => any;

const REDACTION: Record<string, Fn> = {
    luhn_valid: a => luhnValid(a.digits),
    verhoeff_valid: a => verhoeffValid(a.digits),
    redact_text: (a, p) => redactText(a.text, p),
    redact_message_text: (a, p) => redactMessageText(a.text, p),
    redact_headers: (a, p) => redactHeaders(a.headers, p),
    redact_body: (a, p) => redactBody(a.body, a.content_type ?? undefined, p),
    redact_network_events: (a, p) => redactNetworkEvents(a.events, p),
    query_names_only: a => queryNamesOnly(a.raw),
    redact_hook_report: (a, p) => redactHookReport(a.report, p),
    redact_page_budgets: (a, p) => redactPageBudgets(a.items, p),
    redact_a11y_target: (a, p) => redactA11yTarget(a.selector, p),
    a11y_help_url: a => a11yHelpUrl(a.raw),
    redact_accessibility_results: (a, p) => redactAccessibilityResults(a.items, p),
    redact_crawl_report: (a, p) => redactCrawlReport(a.report, p),
    redact_step_evidence: (a, p) => redactStepEvidence(a.entries, p),
    redact_access_observation: (a, p) => redactAccessObservation(a.obs, p),
    redact_har: (a, p) => redactHar(a.har, p),
    redact_job_result: (a, p) => redactJobResult(a.result, p),
    redact_dom: (a, p) => redactDom(a.html, {
        policy: p, maskSelectors: a.mask_selectors ?? [], ...(a.max_chars ? { maxChars: a.max_chars } : {}),
    }),
};

function elementById(html: string, id: string) {
    const root = buildTree(html);
    for (const el of walk(root)) if (el.attrs.get('id') === id) return { root, el };
    throw new Error(`vector html has no element with id=${id}`);
}

const HEAL: Record<string, Fn> = {
    tokens: a => tokens(a.text),
    similarity: a => similarity(a.query, a.features),
    looks_generated: a => looksGenerated(a.value),
    classify_selector: a => classifySelector(a.selector),
    score_candidate: a => scoreCandidate(a.breakdown),
    implicit_role: a => implicitRole(elementById(a.html, a.target_id).el),
    accessible_name: a => {
        const { root, el } = elementById(a.html, a.target_id);
        return accessibleName(el, root);
    },
    deterministic_candidates: a => deterministicCandidates(a.html, a.old_selector, a.intent)
        .map(({ selector, strategy, stability, similarity: sim, target }) =>
            ({ selector, strategy, stability, similarity: sim, target })),
    // Match counts come from the vector (the Python twin derives them from
    // the snapshot and checks they equal these); `same_element` lists the
    // groups of selectors that resolve to one element.
    rank_candidates: async a => rankHealCandidates({
        html: a.html, oldSelector: a.old_selector, intent: a.intent,
        llmSelector: a.llm_selector, llmConfidence: a.llm_confidence,
        count: async sel => a.counts?.[sel] ?? 0,
        sameElement: async (x, y) => (a.same_element || []).some((g: string[]) => g.includes(x) && g.includes(y)),
    }),
};

const actual: Record<string, any> = {};

function expectedFor(v: Vector): any {
    if (v.known_divergence) {
        assert.ok(v.expected_by_twin, `${v.id}: known_divergence needs expected_by_twin`);
        return v.expected_by_twin.typescript;
    }
    return v.expected;
}

/** Drop `undefined` members so a TS result compares like its JSON form. */
const asJson = (x: any) => (x === undefined ? null : JSON.parse(JSON.stringify(x)));

async function check(file: string, fns: Record<string, Fn>) {
    const vectors = load(file);
    assert.ok(vectors.length > 0);
    const ids = new Set<string>();
    // Every vector runs before anything is asserted, so one report lists all
    // the disagreements instead of the first.
    const failures: string[] = [];
    for (const v of vectors) {
        assert.ok(!ids.has(v.id), `duplicate vector id ${v.id}`);
        ids.add(v.id);
        const fn = fns[v.fn];
        assert.ok(fn, `${v.id}: no TypeScript binding for fn ${v.fn}`);
        const policy = v.data_policy === undefined ? undefined : policyFromDataPolicy(v.data_policy);
        const out = asJson(await fn(v.args, policy));
        actual[v.id] = out;
        const want = expectedFor(v);
        try {
            if (v.compare === 'json') assert.deepEqual(JSON.parse(out), JSON.parse(want));
            else assert.deepEqual(out, want);
        } catch {
            failures.push(`${v.id}\n  expected: ${JSON.stringify(want)}\n  actual:   ${JSON.stringify(out)}`);
        }
    }
    assert.equal(failures.length, 0, `${failures.length} vector(s) disagree:\n${failures.join('\n')}`);
}

test('redaction twin vectors', async () => { await check('redaction.json', REDACTION); });
test('heal ranking twin vectors', async () => { await check('heal_ranking.json', HEAL); });

test.after(() => {
    const dump = process.env.TWIN_VECTORS_ACTUAL;
    if (dump) fs.writeFileSync(dump, JSON.stringify(actual, null, 2));
});
