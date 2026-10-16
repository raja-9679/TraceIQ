import { api } from '@/lib/api';

// Mirrors backend/app/api/journey_reports.py and benchmarks.py (WP12c).

// --- journey coverage ------------------------------------------------------

export interface JourneyImport {
    id: number;
    label: string | null;
    filename: string | null;
    source_format: 'csv' | 'json';
    row_count: number;
    skipped_rows: number;
    path_count: number;
    total_views: number;
    period_start: string | null;
    period_end: string | null;
    warnings: string[];
    journeys: string[];
    created_by_id: number | null;
    created_at: string | null;
}

export interface ImportList {
    imports: JourneyImport[];
    limits: { max_bytes: number; max_rows: number; kept: number };
}

export type RuleKind = 'pattern' | 'regex';
export interface PathRule { pattern: string; kind: RuleKind; name?: string }

export interface CoverageEntry { key: string; views: number; share_pct: number | null; paths: number; case_ids: number[] }
export interface JourneyStep { key: string; step: number | null; views: number; covered: boolean; case_ids: number[] }
export interface JourneyRow {
    journey: string;
    steps: JourneyStep[];
    step_count: number;
    covered_steps: number;
    coverage_pct: number | null;
    weighted_coverage_pct: number | null;
    views: number;
    first_gap: string | null;
}
export interface CoverageReport {
    total_views: number;
    covered_views: number;
    coverage_pct: number | null;
    distinct_keys: number;
    covered_keys: number;
    tested_keys: number;
    top_uncovered: CoverageEntry[];
    covered_low_traffic: CoverageEntry[];
    covered: CoverageEntry[];
    low_traffic_share_pct: number;
    journeys: JourneyRow[];
}
export interface CoverageResponse {
    import: JourneyImport | null;
    report: CoverageReport | null;
    case_names?: Record<string, string>;
    tested_keys?: string[];
}

export const listJourneyImports = async (projectId: number): Promise<ImportList> =>
    (await api.get(`/projects/${projectId}/journey-imports`)).data;

export const uploadJourneyImport = async (
    projectId: number, file: File, opts: { label?: string; format?: '' | 'csv' | 'json' } = {},
): Promise<JourneyImport> => {
    const form = new FormData();
    form.append('file', file);
    if (opts.label) form.append('label', opts.label);
    if (opts.format) form.append('format', opts.format);
    return (await api.post(`/projects/${projectId}/journey-imports`, form)).data;
};

export const deleteJourneyImport = async (importId: number): Promise<void> => {
    await api.delete(`/journey-imports/${importId}`);
};

export const getJourneyRules = async (projectId: number): Promise<{ path_rules: PathRule[]; max_rules: number }> =>
    (await api.get(`/projects/${projectId}/journey-rules`)).data;

export const putJourneyRules = async (projectId: number, rules: PathRule[]): Promise<{ path_rules: PathRule[] }> =>
    (await api.put(`/projects/${projectId}/journey-rules`, { path_rules: rules })).data;

export const getJourneyCoverage = async (projectId: number, importId?: number | null): Promise<CoverageResponse> =>
    (await api.get(`/projects/${projectId}/journey-coverage`, { params: importId ? { import_id: importId } : {} })).data;

// --- business journeys -------------------------------------------------------

export type Criticality = 'low' | 'medium' | 'high' | 'critical';
export type Health = 'passing' | 'failing' | 'flaky' | 'not_run';

export interface JourneyCase {
    id: number;
    name: string | null;
    quarantined: boolean;
    state: Health;
    last_status: string | null;
    last_run_id: number | null;
    last_run_at: string | null;
}
export interface JourneyRequirement {
    ref: string;
    title: string | null;
    url: string | null;
    cases: number;
    project_cases: number;
    status: Health | 'uncovered';
}
export interface JourneyTraffic {
    source: 'journey' | 'pages';
    views: number;
    share_pct?: number | null;
    pages?: number;
    coverage_pct?: number | null;
    weighted_coverage_pct?: number | null;
    step_count?: number;
    covered_steps?: number;
    first_gap?: string | null;
    import_id: number;
}
export interface BusinessJourney {
    id: number;
    project_id: number;
    name: string;
    description: string | null;
    owner_id: number | null;
    owner?: { id: number; name: string; email: string } | null;
    criticality: Criticality;
    case_ids: number[];
    requirement_refs: string[];
    traffic_journey: string | null;
    health: Health;
    cases: JourneyCase[];
    counts: Record<Health, number>;
    last_run_at: string | null;
    trend: { date: string; passed: number; total: number; pass_rate: number | null }[];
    missing_case_ids: number[];
    requirements: JourneyRequirement[];
    requirements_covered: number;
    traffic: JourneyTraffic | null;
}
export interface JourneyBoard {
    journeys: BusinessJourney[];
    window_days: number;
    criticalities: Criticality[];
    latest_import_id: number | null;
}
export interface JourneyInput {
    name: string;
    description?: string | null;
    owner_id?: number | null;
    criticality: Criticality;
    case_ids: number[];
    requirement_refs: string[];
    traffic_journey?: string | null;
}

export const getJourneyBoard = async (projectId: number): Promise<JourneyBoard> =>
    (await api.get(`/projects/${projectId}/business-journeys`)).data;
export const createJourney = async (projectId: number, body: JourneyInput) =>
    (await api.post(`/projects/${projectId}/business-journeys`, body)).data;
export const updateJourney = async (journeyId: number, body: Partial<JourneyInput>) =>
    (await api.patch(`/business-journeys/${journeyId}`, body)).data;
export const deleteJourney = async (journeyId: number): Promise<void> => {
    await api.delete(`/business-journeys/${journeyId}`);
};

export interface CaseSummary { id: number; name: string; test_suite_id: number | null }
/** Every case of the project (the list endpoint pages at 200). */
export const listProjectCases = async (projectId: number): Promise<CaseSummary[]> => {
    const out: CaseSummary[] = [];
    for (let offset = 0; offset < 2000; offset += 200) {
        const { data } = await api.get('/cases', { params: { project_id: projectId, limit: 200, offset } });
        out.push(...data.items);
        if (out.length >= data.total || data.items.length === 0) break;
    }
    return out;
};

// --- flags ---------------------------------------------------------------------

export interface FlagCatalogEntry { id: number; project_id: number; name: string; values: string[]; description: string | null }
export interface FlagValueStat {
    value: string;
    runs: number;
    cases: number;
    passed_runs: number;
    failed_runs: number;
    last_seen: string | null;
    last_run_id: number | null;
    in_catalog: boolean;
}
export interface FlagStat {
    name: string;
    in_catalog: boolean;
    catalog_id: number | null;
    description: string | null;
    catalog_values: string[];
    values: FlagValueStat[];
    never_tested: string[];
    runs: number;
}
export interface FlagCoverage {
    runs_with_flags: number;
    flags: FlagStat[];
    uncatalogued: string[];
    never_tested_total: number;
    days: number;
    truncated: boolean;
}

export const listFlagCatalog = async (projectId: number): Promise<{ flags: FlagCatalogEntry[] }> =>
    (await api.get(`/projects/${projectId}/flag-catalog`)).data;
export const createFlag = async (projectId: number, body: { name: string; values: string[]; description?: string | null }) =>
    (await api.post(`/projects/${projectId}/flag-catalog`, body)).data;
export const updateFlag = async (flagId: number, body: { values?: string[]; description?: string | null }) =>
    (await api.patch(`/flag-catalog/${flagId}`, body)).data;
export const deleteFlag = async (flagId: number): Promise<void> => {
    await api.delete(`/flag-catalog/${flagId}`);
};
export const getFlagCoverage = async (projectId: number, days: number): Promise<FlagCoverage> =>
    (await api.get(`/projects/${projectId}/flag-coverage`, { params: { days } })).data;

// --- benchmarks ------------------------------------------------------------------

export interface BenchmarkTarget { label: string; base_url: string }
export interface BenchmarkGroup {
    id: number;
    project_id: number;
    test_suite_id: number;
    name: string;
    targets: BenchmarkTarget[];
    browser: string;
    created_by_id: number | null;
    created_at: string | null;
    run_ids?: number[];
    status?: 'running' | 'complete' | 'no runs';
}
export interface BenchmarkSite {
    index: number;
    label: string;
    base_url: string;
    run_id: number | null;
    status: string;
    total: number;
    passed: number;
    failed: number;
    pass_rate: number | null;
    duration_ms: number | null;
    avg_case_ms: number | null;
    budgets: Record<string, number>;
    budget_samples: number;
}
export interface BenchmarkCell { status: string; duration_ms: number | null }
export interface BenchmarkTestRow {
    key: string;
    test_name: string | null;
    test_case_id: number | null;
    cells: (BenchmarkCell | null)[];
    differs: boolean;
}
export interface BenchmarkDetail extends BenchmarkGroup {
    suite_name: string | null;
    matrix: {
        sites: BenchmarkSite[];
        tests: BenchmarkTestRow[];
        metrics: string[];
        summary: {
            best_pass_rate: string | null;
            worst_pass_rate: string | null;
            fastest: string | null;
            differing_tests: number;
            complete: boolean;
        };
    };
    absolute_goto_cases: { id: number; name: string }[];
}

export const listBenchmarks = async (projectId: number): Promise<{
    groups: BenchmarkGroup[]; limits: { min_targets: number; max_targets: number }; browsers: string[];
}> => (await api.get(`/projects/${projectId}/benchmark-groups`)).data;
export const createBenchmark = async (projectId: number, body: {
    name: string; test_suite_id: number; targets: BenchmarkTarget[]; browser: string;
}): Promise<BenchmarkGroup> => (await api.post(`/projects/${projectId}/benchmark-groups`, body)).data;
export const getBenchmark = async (groupId: number): Promise<BenchmarkDetail> =>
    (await api.get(`/benchmark-groups/${groupId}`)).data;
export const rerunBenchmark = async (groupId: number): Promise<BenchmarkGroup> =>
    (await api.post(`/benchmark-groups/${groupId}/rerun`)).data;
export const deleteBenchmark = async (groupId: number): Promise<void> => {
    await api.delete(`/benchmark-groups/${groupId}`);
};
