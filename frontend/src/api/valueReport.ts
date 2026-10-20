import { api } from '@/lib/api';

// Mirrors backend/app/api/value_report.py and app/services/value_report.py (WP16d, V6).

export type MetricKind = 'measured' | 'estimated';

interface MetricBase<K extends string, V> {
    key: K;
    title: string;
    kind: MetricKind;
    definition: string;
    data_source: string;
    sample_size: number;
    sample_label: string;
    /** null = no inputs; `missing` then says why (never shown as 0). */
    value: V | null;
    missing: string | null;
}

export type DiagnosisSignal = 'triage' | 'ticket' | 'quarantine' | 'fixed';

export type DiagnosisMetric = MetricBase<'time_to_diagnosis', {
    median_hours: number;
    p90_hours: number;
    diagnosed: number;
    undiagnosed: number;
    by_signal: Record<DiagnosisSignal, number>;
}> & { clusters_considered: number };

export type AccuracyMetric = MetricBase<'analysis_accuracy', {
    percent: number;
    correct: number;
    incorrect: number;
    split: number;
    votes: number;
    by_source: Record<string, { correct: number; incorrect: number }>;
}> & { min_sample: number; rated: number; split: number };

export interface TargetedRunEstimate {
    first_run_id: number;
    created_at: string;
    cases_ran: number;
    cases_total: number;
    targeted_browser_minutes: number | null;
    baseline_first_run_id: number;
    baseline_browser_minutes: number;
    saved_browser_minutes: number;
    saved_wall_minutes: number | null;
}

export type SavingsMetric = MetricBase<'targeted_time_saved', {
    saved_browser_minutes: number;
    saved_wall_minutes: number | null;
    wall_samples: number;
    cases_skipped: number;
    cases_ran: number;
}> & {
    formula: string;
    measured_inputs: { targeted_runs: number; estimated: number; no_baseline: number; not_estimable: number };
    runs: TargetedRunEstimate[];
};

export interface ProposalCounts {
    total: number;
    pending: number;
    accepted: number;
    auto_applied: number;
    rejected: number;
    other: number;
    decided: number;
    accept_rate: number | null;
    auto_apply_rate: number | null;
    reject_rate: number | null;
}

export type ProposalMetric = MetricBase<'proposal_outcomes', {
    case: ProposalCounts;
    heal: ProposalCounts;
    revert: { reverted: number; kept: number; not_measurable: number; revert_rate: number | null };
    repair_validation: {
        validation_runs: number;
        passed_matched: number;
        passed_unmatched: number;
        failed: number;
        success_rate: number | null;
        decisions: number;
        decisions_validated: number;
        validated_share: number | null;
    };
}> & { sub_definitions: { revert: string; repair_validation: string } };

export type ValueMetric = DiagnosisMetric | AccuracyMetric | SavingsMetric | ProposalMetric;

export interface ValueReport {
    scope: {
        kind: 'project' | 'workspace';
        project_id?: number;
        project_name?: string;
        workspace_id?: number;
        workspace_name?: string;
        projects: { id: number; name: string }[];
    };
    window: { start: string; end: string };
    generated_at: string;
    metrics: ValueMetric[];
    links: { label: string; path: string; reason: string }[];
    available_projects?: { id: number; name: string }[];
}

export type HypothesisVerdict = 'correct' | 'incorrect';

export interface HypothesisFeedbackRow {
    index: number;
    hypothesis_key: string;
    correct: number;
    incorrect: number;
    mine: HypothesisVerdict | null;
}

export interface RunHypothesisFeedback {
    run_id: number;
    can_rate: boolean;
    is_validation: boolean;
    /** keyed by result id */
    results: Record<string, HypothesisFeedbackRow[]>;
}

export const valueReportApi = {
    project: (projectId: number, start: string, end: string) =>
        api.get<ValueReport>(`/projects/${projectId}/value-report`, { params: { start, end } }).then((r) => r.data),
    workspace: (workspaceId: number, start: string, end: string) =>
        api.get<ValueReport>(`/workspaces/${workspaceId}/value-report`, { params: { start, end } }).then((r) => r.data),
};

export const hypothesisFeedbackApi = {
    forRun: (runId: number) =>
        api.get<RunHypothesisFeedback>(`/runs/${runId}/hypothesis-feedback`).then((r) => r.data),
    rate: (runId: number, resultId: number, body: { index: number; hypothesis_key: string; verdict: HypothesisVerdict }) =>
        api.put(`/runs/${runId}/results/${resultId}/hypothesis-feedback`, body).then((r) => r.data),
    clear: (runId: number, resultId: number, index: number, hypothesisKey: string) =>
        api.delete(`/runs/${runId}/results/${resultId}/hypothesis-feedback`, {
            params: { index, hypothesis_key: hypothesisKey },
        }),
};
