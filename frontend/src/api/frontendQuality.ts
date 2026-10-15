import { api } from '@/lib/api';

// Mirrors backend/app/api/frontend_quality.py (WP10).

export interface ViewportSpec { name: string; width: number; height: number }
export interface LocaleSpec {
    locale: string;
    query?: Record<string, string>;
    cookie?: { name: string; value: string; domain?: string };
    url_template?: string;
}
export interface Emulation {
    viewport?: ViewportSpec;
    locale?: LocaleSpec;
    color_scheme?: 'light' | 'dark' | 'no-preference';
    reduced_motion?: boolean;
    label?: string;
    step_screenshots?: boolean;
}

export interface MatrixPresets {
    viewports: ViewportSpec[];
    color_schemes: string[];
    max_fanout_runs: number;
    rtl_languages: string[];
}

export interface SweepStep {
    index: number;
    step_id: string | null;
    type: string;
    status: string;
    screenshot: string | null;
    error: string | null;
}
export interface SweepVariant {
    run_id: number;
    test_case_id: number | null;
    test_case_name: string | null;
    browser: string;
    device: string | null;
    status: string;
    label: string;
    emulation: Emulation;
    rtl: boolean;
    tests: { result_id: number; test_name: string; test_case_id: number | null; status: string;
        error_message: string | null; steps: SweepStep[] }[];
}

export type CrawlCategory = 'broken' | 'network' | 'slow' | 'redirect_loop' | 'mixed_content' | 'redirect_chain' | 'not_checked';
export const CRAWL_CATEGORIES: CrawlCategory[] = ['broken', 'network', 'slow', 'redirect_loop', 'redirect_chain', 'mixed_content', 'not_checked'];
export const CRAWL_ELEMENTS = ['a', 'img', 'script', 'link', 'css', 'page'] as const;

export interface CrawlRun {
    id: number;
    crawl_id: number;
    project_id: number;
    status: 'pending' | 'running' | 'completed' | 'error';
    trigger: string;
    created_at: string;
    completed_at: string | null;
    pages_crawled: number;
    links_checked: number;
    issue_counts: Record<CrawlCategory, number> | null;
    truncated: boolean;
    error: string | null;
    robots: { respected: boolean; fetched: boolean; skipped: number; crawl_delay_ms: number | null } | null;
    start_url: string | null;
    pages?: { url: string; depth: number; status: number | null; load_ms: number | null; links: number; issues: number; error?: string | null }[];
}

export interface SiteCrawl {
    id: number;
    project_id: number;
    name: string;
    start_url: string;
    max_depth: number;
    max_pages: number;
    extra_hosts: string[] | null;
    respect_robots: boolean;
    rate_limit_ms: number;
    slow_threshold_ms: number;
    check_external: boolean;
    cron_expression: string | null;
    is_active: boolean;
    next_run_at: string | null;
    last_run_at: string | null;
    last_run: CrawlRun | null;
}

export type SiteCrawlInput = Omit<SiteCrawl, 'id' | 'project_id' | 'next_run_at' | 'last_run_at' | 'last_run' | 'extra_hosts'> & {
    extra_hosts: string[];
};

export interface CrawlIssue {
    id: number;
    page_url: string;
    target_url: string;
    element: string;
    category: CrawlCategory;
    status_code: number | null;
    duration_ms: number | null;
    redirect_chain: { url: string; status: number | null }[] | null;
    detail: string | null;
}

export interface IssueFilters {
    category?: CrawlCategory[];
    element?: string[];
    q?: string;
    page_url?: string;
    limit?: number;
    offset?: number;
}

export interface BudgetSample {
    id: number;
    test_run_id: number;
    test_case_id: number | null;
    test_name: string | null;
    step_id: string | null;
    page_url: string;
    page_key: string;
    variant: string | null;
    metrics: Record<string, number>;
    checks: { metric: string; value: number | null; budget: number | null; passed: boolean }[];
    seo: { check: string; passed: boolean; severity: string; value: string | null; detail: string | null }[];
    passed: boolean;
    created_at: string;
}

export interface BudgetPageSummary {
    page_key: string;
    variant: string | null;
    latest: BudgetSample;
    samples: number;
    failed: number;
}

export interface SelectorHealthEntry {
    test_case_id: number;
    case_name: string | null;
    suite_id: number | null;
    selector: string;
    step_ids: string[];
    heal_count: number;
    failure_count: number;
    runtime_healed_count: number;
    total: number;
    first_seen: string | null;
    last_seen: string | null;
    recent_run_ids: number[];
    weeks: { week: string; heals: number; failures: number }[];
    fragility: string[];
    latest_heal: { new_selector: string; status: string; confidence: number } | null;
    suggestions: { locator: string; source: 'dom' | 'heal'; reason: string }[];
}

export interface Story { id: string; title: string; name: string; tags: string[] }

function qs(f: IssueFilters): string {
    const p = new URLSearchParams();
    (f.category || []).forEach(c => p.append('category', c));
    (f.element || []).forEach(e => p.append('element', e));
    if (f.q) p.set('q', f.q);
    if (f.page_url) p.set('page_url', f.page_url);
    p.set('limit', String(f.limit ?? 200));
    p.set('offset', String(f.offset ?? 0));
    return p.toString();
}

export const frontendQualityApi = {
    presets: async (): Promise<MatrixPresets> => (await api.get('/frontend-matrix/presets')).data,
    sweep: async (runId: number): Promise<{ sweep_group: string | null; variants: SweepVariant[] }> =>
        (await api.get(`/runs/${runId}/sweep`)).data,

    hosts: async (projectId: number): Promise<string[]> =>
        (await api.get(`/projects/${projectId}/frontend-hosts`)).data.hosts,
    listCrawls: async (projectId: number): Promise<SiteCrawl[]> =>
        (await api.get(`/projects/${projectId}/site-crawls`)).data,
    createCrawl: async (projectId: number, body: SiteCrawlInput): Promise<SiteCrawl> =>
        (await api.post(`/projects/${projectId}/site-crawls`, body)).data,
    updateCrawl: async (crawlId: number, body: Partial<SiteCrawlInput>): Promise<SiteCrawl> =>
        (await api.patch(`/site-crawls/${crawlId}`, body)).data,
    deleteCrawl: async (crawlId: number): Promise<void> => { await api.delete(`/site-crawls/${crawlId}`); },
    runCrawl: async (crawlId: number): Promise<CrawlRun> => (await api.post(`/site-crawls/${crawlId}/run`)).data,
    crawlRuns: async (crawlId: number): Promise<CrawlRun[]> => (await api.get(`/site-crawls/${crawlId}/runs`)).data,
    crawlRun: async (runId: number): Promise<CrawlRun> => (await api.get(`/site-crawl-runs/${runId}`)).data,
    crawlIssues: async (runId: number, f: IssueFilters): Promise<{ total: number; items: CrawlIssue[] }> =>
        (await api.get(`/site-crawl-runs/${runId}/issues?${qs(f)}`)).data,

    budgetSummary: async (projectId: number, days = 30): Promise<BudgetPageSummary[]> =>
        (await api.get(`/projects/${projectId}/page-budgets?days=${days}`)).data,
    budgetTrend: async (projectId: number, pageKey: string, variant: string | null): Promise<BudgetSample[]> => {
        const p = new URLSearchParams({ page_key: pageKey });
        if (variant) p.set('variant', variant);
        return (await api.get(`/projects/${projectId}/page-budgets/trend?${p.toString()}`)).data;
    },

    selectorHealth: async (projectId: number, days = 90): Promise<{ days: number; entries: SelectorHealthEntry[];
        totals: { selectors: number; heals: number; failures: number } }> =>
        (await api.get(`/projects/${projectId}/selector-health?days=${days}`)).data,

    stories: async (projectId: number, url: string): Promise<{ root: string; stories: Story[] }> =>
        (await api.post(`/projects/${projectId}/storybook/stories`, { url })).data,
    generateStoryCases: async (projectId: number, body: { url: string; suite_id: number; story_ids: string[];
        visual: boolean; a11y: boolean; a11y_impact: string }): Promise<{ created: { id: number; name: string }[];
        updated: { id: number; name: string }[] }> =>
        (await api.post(`/projects/${projectId}/storybook/cases`, body)).data,
};
