// Pure decision: which cases a TraceIQ PR run executes. Strict by design —
// any doubt about coverage runs the full suite, with the reason kept for the
// PR comment (docs/superpowers/specs/2026-10-14-targeted-runs-design.md).

function fallback(reason) {
    return { kind: 'fallback', caseIds: [], skippedQuarantined: [], reason };
}

function decideSelection({ mode, isPullRequest, impact, impactError }) {
    if (mode !== 'targeted') return { kind: 'suite', caseIds: [], skippedQuarantined: [], reason: null };
    if (!isPullRequest) return fallback('not a pull request — no diff to select from');
    if (impactError) return fallback(`impact analysis failed (${impactError.message || impactError})`);
    const unmatched = impact?.unmatched_files || [];
    if (unmatched.length) {
        const shown = unmatched.slice(0, 3).map((f) => `\`${f}\``).join(', ');
        const more = unmatched.length > 3 ? ` and ${unmatched.length - 3} more` : '';
        return fallback(`${shown}${more} ${unmatched.length === 1 ? 'has' : 'have'} no mapped test`);
    }
    const matched = impact?.matched_cases || [];
    if (!matched.length) return fallback('no test case matched the changed files');
    const skippedQuarantined = matched.filter((c) => c.flake && c.flake.is_quarantined);
    const runnable = matched.filter((c) => !(c.flake && c.flake.is_quarantined));
    if (!runnable.length) return fallback('every matched test case is quarantined');
    return { kind: 'targeted', caseIds: runnable.map((c) => c.id), skippedQuarantined, reason: null };
}

/** Matched cases that ran AND were flagged for review — a quarantined case
 * is reported as skipped, so it must never also appear as "ran". */
function reviewNotes(decision, matchedCases) {
    const ran = new Set(decision.caseIds);
    return (matchedCases || []).filter((c) => c.suggested_action === 'review' && ran.has(c.id));
}

module.exports = { decideSelection, reviewNotes };
