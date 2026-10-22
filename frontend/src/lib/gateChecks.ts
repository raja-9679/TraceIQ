// The quality gate's check ids, in words a release manager reads, with where
// the evidence for each one lives. Shared by the Quality gate and Release
// readiness pages so neither shows a raw id.
//
// Ids come from backend/app/api/endpoints/quality.py (evaluate_gate_for_runs),
// app/services/release_readiness.py, app/services/accessibility.py:gate_check
// and app/services/spec_versions.py:breaking_change_check.

export type GateCheckInfo = {
    title: string;
    explain: string;
    links: { tag: string; text: string; to: string }[];
};

const RUNS_LINK = { tag: 'runs', text: 'See the runs', to: '/runs' };

export const GATE_CHECKS: Record<string, GateCheckInfo> = {
    pass_rate: {
        title: 'Pass rate', explain: 'Share of finished runs that passed among the runs this gate evaluated. Cancelled and timed-out runs carry no verdict and are not counted.',
        links: [{ tag: 'runs', text: 'See the evaluated runs', to: '/runs' }, { tag: 'triage', text: 'Group the failures', to: '/triage' }],
    },
    coverage: {
        title: 'Coverage', explain: 'How much of the suite the evaluated runs covered: the full suite, or a targeted run of some cases. Informational only; it never fails the gate.',
        links: [RUNS_LINK],
    },
    // _severity_counts(run_ids=...): findings attached to the evaluated runs
    // (passive checks and leak detection during those runs), whatever their
    // workflow status. Standalone scans have no run and are not counted.
    security_high_findings: {
        title: 'High-severity security findings', explain: 'High findings recorded during the runs this gate evaluated (passive checks and leak detection), whatever their status. Standalone security scans are not counted.',
        links: [{ tag: 'security', text: 'Open Security', to: '/security' }],
    },
    security_medium_findings: {
        title: 'Medium-severity security findings', explain: 'Medium findings recorded during the runs this gate evaluated (passive checks and leak detection), whatever their status. Standalone security scans are not counted.',
        links: [{ tag: 'security', text: 'Open Security', to: '/security' }],
    },
    monitors_up: {
        title: 'Monitors are up', explain: 'Every production monitor of this project must be up.',
        links: [{ tag: 'watch', text: 'Open Watch', to: '/monitors' }],
    },
    external_tests: {
        title: 'External CI tests', explain: 'The latest JUnit report pushed from CI (for the commit, when one is evaluated) must have no failures.',
        links: [],
    },
    perf_lcp: { title: 'Largest Contentful Paint budget', explain: 'Worst LCP across the evaluated runs.', links: [RUNS_LINK] },
    perf_cls: { title: 'Layout shift budget', explain: 'Worst CLS across the evaluated runs.', links: [RUNS_LINK] },
    perf_ttfb: { title: 'Time to first byte budget', explain: 'Worst TTFB across the evaluated runs.', links: [RUNS_LINK] },
    flaky_cases: {
        title: 'Flaky tests', explain: 'Tests with an active quarantine or a flakiness score of 0.4 or more must not exceed the limit.',
        links: [{ tag: 'flaky', text: 'Open Flaky tests', to: '/flaky-tests' }],
    },
    critical_passed: {
        title: 'Critical tests passed', explain: 'Every test with priority critical must have passed on the evaluated commit, in every browser and device it ran on (newest result counts).',
        links: [{ tag: 'readiness', text: 'Open Release readiness', to: '/release-readiness' }],
    },
    evidence_age: {
        title: 'Evidence age', explain: 'The newest pass of each critical test (every test when none is critical) must be recent enough.',
        links: [{ tag: 'readiness', text: 'Open Release readiness', to: '/release-readiness' }],
    },
    manual_signoff: {
        title: 'Release sign-off', explain: 'The newest manual-testing sign-off (for the commit, when one is evaluated) must be an approval.',
        links: [{ tag: 'signoff', text: 'Open Release sign-off', to: '/release-signoff' }],
    },
    accessibility_new_violations: {
        title: 'New accessibility violations', explain: 'No new violation of impact serious or critical in the evaluated runs, compared with each test’s previous run. Waived ones are left out.',
        links: [{ tag: 'a11y', text: 'Open Accessibility', to: '/accessibility' }],
    },
    api_breaking_changes: {
        title: 'Breaking API changes', explain: 'The newest stored OpenAPI spec version must have no breaking change against the baseline (the previous version, or a pinned one).',
        links: [],
    },
};

export function describeGateCheck(name: string): GateCheckInfo {
    return GATE_CHECKS[name] ?? { title: name.replace(/_/g, ' '), explain: '', links: [] };
}

export const gateCheckTitle = (name: string): string => describeGateCheck(name).title;

/** Readiness reasons arrive as "<check id>: <actual> (needs <threshold>)"
 *  (release_readiness.verdict_for); swap the id for its title. */
export function labelReason(reason: string): string {
    const m = /^([a-z0-9_]+): /.exec(reason);
    return m && GATE_CHECKS[m[1]] ? `${GATE_CHECKS[m[1]].title}: ${reason.slice(m[0].length)}` : reason;
}
