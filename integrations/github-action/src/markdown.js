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

module.exports = { mdCell, gateSection };
