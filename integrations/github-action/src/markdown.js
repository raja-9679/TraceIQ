'use strict';

// Markdown helpers for the PR comment. Pure — tested in markdown.test.js.

function mdCell(text, maxLen = 200) {
    if (text === null || text === undefined) return '';
    let s = String(text).replace(/\r?\n/g, ' ').replace(/\|/g, '\\|').trim();
    if (s.length > maxLen) s = `${s.slice(0, maxLen - 1)}…`;
    return s;
}

// Renders whatever checks the server returns — new gate checks need no action
// release. `detail` carries the reason (e.g. which critical case is missing),
// and `missing_data` separates "no evidence" from "something failed".
function gateSection(gate, enforced) {
    if (!gate) return [];
    const lines = [
        '### Quality gate',
        '',
        `**${gate.passed ? ':white_check_mark: PASS' : ':x: FAIL'}**` +
        (enforced ? '' : ' _(advisory — not enforced for this project)_'),
        '',
        '| Check | Result | Actual | Threshold | Detail |',
        '| --- | --- | --- | --- | --- |',
    ];
    for (const c of gate.checks || []) {
        const icon = c.passed ? ':white_check_mark:' : c.missing_data ? ':grey_question: no data' : ':x:';
        lines.push(
            `| ${mdCell(c.name)} | ${icon} | ` +
            `${mdCell(c.actual)} | ${mdCell(c.threshold)} | ${mdCell(c.detail, 300)} |`,
        );
    }
    return [...lines, ''];
}

// Code spans can't escape backticks, so pick a fence longer than any run
// of backticks inside (CommonMark), padding when the content touches one.
function mdCode(text, maxLen = 120) {
    const s = mdCell(text, maxLen);
    if (!s) return '';
    const longest = Math.max(0, ...(s.match(/`+/g) || []).map((m) => m.length));
    const fence = '`'.repeat(longest + 1);
    const pad = s.startsWith('`') || s.endsWith('`') ? ' ' : '';
    return `${fence}${pad}${s}${pad}${fence}`;
}

function fmtTtl(seconds) {
    if (!seconds) return '';
    return seconds >= 3600 ? `${Math.round(seconds / 3600)}h` : `${Math.round(seconds / 60)}m`;
}

// One entry per failing case: the failing step, the error (truncated), a
// short-lived screenshot link and the run page. `groups` is
// [{ runId, failures: [...] }] from GET /api/runs/{id}/failures.
function failuresSection(groups, baseUrl, maxCases = 20) {
    const all = [];
    for (const g of groups || []) {
        for (const f of (g && g.failures) || []) all.push({ runId: g.runId, ...f });
    }
    if (all.length === 0) return [];
    const lines = ['### Failing cases', ''];
    for (const f of all.slice(0, maxCases)) {
        const runUrl = `${baseUrl}/runs/${f.runId}`;
        const step = f.failing_step;
        const stepText = step
            ? `step ${Number(step.index) + 1} ${mdCode(step.type, 40)}${step.selector ? ` on ${mdCode(step.selector)}` : ''}`
            : 'no step evidence';
        lines.push(`- :x: **${mdCell(f.test_name, 120)}** — ${stepText}`);
        const err = (step && step.error) || f.error;
        if (err) lines.push(`  > ${mdCell(err, 300)}`);
        const links = [];
        if (f.screenshot_url) {
            const ttl = fmtTtl(f.screenshot_expires_in);
            links.push(`[Screenshot](${f.screenshot_url})${ttl ? ` _(link expires in ${ttl})_` : ''}`);
        }
        links.push(`[Run #${f.runId}](${runUrl})`);
        lines.push(`  ${links.join(' · ')}`);
    }
    if (all.length > maxCases) lines.push('', `_…and ${all.length - maxCases} more failing case(s); see TraceIQ._`);
    return [...lines, ''];
}

module.exports = { mdCell, mdCode, gateSection, failuresSection };
