// Non-component helpers for components/evidence/* (kept apart so those files
// export only components, which React fast refresh needs).
//
// Shapes mirror the worker's `step_evidence` (execution-engine/src/core/
// evidence.ts, normalised by result_aggregator.normalise_step_evidence) and
// FailureReport v2 (backend/app/schemas/failure_report.py). Every field is
// optional: results from older workers carry no step evidence, and v1
// reports carry none of the investigation fields.

export interface EvidenceConsoleItem { id: string; type: string; text: string }
export interface EvidenceRequestItem {
    id: string; method: string; url: string; status?: number; failure?: string; resource_type?: string;
}

export interface StepEvidence {
    id: string;
    index: number;
    step_id?: string;
    type: string;
    selector?: string;
    status: 'passed' | 'failed' | 'skipped' | 'healed';
    duration_ms?: number;
    error?: string;
    console?: EvidenceConsoleItem[];
    requests?: EvidenceRequestItem[];
    screenshot?: string;
    dom?: string;
    dom_scope?: 'target' | 'page';
    /** WP16a: ids this step propagated in `traceparent` (trace correlation). */
    trace?: { trace_id: string; span_id: string; requests?: number };
}

export interface EvidenceItem { id: string; kind: string; label: string; step_index?: number | null }
export interface ObservedFact { text: string; evidence_refs: string[] }
export interface Hypothesis {
    category: string; fix_target: string; summary: string; confidence: number;
    evidence_refs: string[]; source: string;
}
export interface FailureHistory {
    runs_considered: number;
    prior_failures: number;
    failing_streak: number;
    first_failing_run_id?: number | null;
    first_failing_commit?: string | null;
    last_pass_run_id?: number | null;
    last_pass_commit?: string | null;
    flip_count: number;
    cluster?: { id: number; title: string; status: string; occurrence_count: number } | null;
}
export interface ChangeRange {
    from_commit: string; to_commit: string; last_pass_run_id: number;
    repo?: string | null; compare_url?: string | null;
}

export interface FailureReportV2 {
    schema_version?: number;
    result_id?: number | null;
    test_name?: string | null;
    root_cause_category?: string;
    summary?: string;
    details?: string;
    suggested_fix?: string | null;
    fix_target?: string;
    confidence?: number;
    analyzed_by?: string;
    observed?: ObservedFact[];
    hypotheses?: Hypothesis[];
    evidence_items?: EvidenceItem[];
    history?: FailureHistory | null;
    changed_since_last_pass?: ChangeRange | null;
    dropped_citations?: number;
}

/** What a clicked evidence reference points at. */
export interface EvidenceFocus {
    resultId: number;
    stepIndex: number | null;
    ref: string;
    // Bumped on every click so re-clicking the same ref scrolls again.
    nonce: number;
}

export function stepEvidenceOf(result: { result_payload?: unknown } | null | undefined): StepEvidence[] {
    const payload = result?.result_payload as { step_evidence?: unknown } | null | undefined;
    const raw = payload?.step_evidence;
    if (!Array.isArray(raw)) return [];
    return raw.filter((e): e is StepEvidence =>
        !!e && typeof e === 'object' && typeof (e as StepEvidence).index === 'number');
}

/** `s3`, `s3.c0`, `s3.dom` → 3. Anything else (error, run:12, console.0) → null. */
export function stepIndexOfRef(ref: string): number | null {
    const m = /^s(\d+)(?:\.|$)/.exec(ref);
    return m ? Number(m[1]) : null;
}

/** `run:12` → 12, else null. */
export function runIdOfRef(ref: string): number | null {
    const m = /^run:(\d+)$/.exec(ref);
    return m ? Number(m[1]) : null;
}

export function stepAnchorId(resultId: number | string, stepIndex: number): string {
    return `evidence-${resultId}-s${stepIndex}`;
}

/** A v2 report has something to show beyond the v1 headline. */
export function isInvestigation(report: FailureReportV2 | null | undefined): boolean {
    return !!report && ((report.observed?.length ?? 0) > 0 || (report.hypotheses?.length ?? 0) > 0);
}

export function evidenceLabel(report: FailureReportV2, ref: string): string {
    return report.evidence_items?.find((i) => i.id === ref)?.label ?? ref;
}

/** Short ref text for a chip: `s3.r0` → "step 4 · request", `run:12` → "run #12". */
export function refChipText(ref: string): string {
    const run = runIdOfRef(ref);
    if (run !== null) return `run #${run}`;
    const step = stepIndexOfRef(ref);
    if (step === null) return ref;
    const sub = ref.split('.')[1];
    const kind = !sub ? '' : sub === 'dom' ? ' · DOM' : sub === 'shot' ? ' · screenshot'
        : sub.startsWith('c') ? ' · console' : sub.startsWith('r') ? ' · request' : '';
    return `step ${step + 1}${kind}`;
}

export function shortCommit(sha?: string | null): string {
    return sha ? sha.slice(0, 8) : '';
}

export function requestOutcome(r: EvidenceRequestItem): string {
    return r.status !== undefined && r.status !== null ? String(r.status) : (r.failure || 'failed');
}

/** The suite-settings keys the console-error card reads and writes. */
export interface SuiteSettingsBlob {
    fail_on_console_errors?: boolean;
    console_error_ignore_patterns?: string[];
    [key: string]: unknown;
}

/** Textarea → ignore-pattern list: one per line, trimmed, blanks dropped, capped at 50 like dispatch. */
export function patternsFromText(text: string): string[] {
    return text.split('\n').map((l) => l.trim()).filter(Boolean).slice(0, 50);
}
