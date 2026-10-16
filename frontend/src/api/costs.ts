import { api } from '@/lib/api';

// Mirrors backend/app/api/costs.py + app/services/cost.py build_report.
// Every amount is null when it cannot be priced (no currency set, or no
// price for that model / for browser-minutes) — never 0.

export interface CostTokens {
    input_tokens: number;
    output_tokens: number;
    tokens: number;
    unpriced_tokens: number;
    amount: number | null;
    priced: boolean;
}

export interface CostProjectRow {
    project_id: number;
    name: string;
    total: number | null;
    budget: number | null;
    month_spent: number | null;
    budget_pct: number | null;
}

export interface CostReport {
    workspace_id: number;
    project_id: number | null;
    window_days: number;
    currency: string | null;
    priced: boolean;
    total: number | null;
    total_is_partial: boolean;
    includes_unassigned: boolean;
    unassigned_total?: number | null;
    llm: CostTokens & {
        by_model: (CostTokens & { provider: string; model: string })[];
        by_feature: (CostTokens & { feature: string })[];
    };
    browser: { minutes: number; amount: number | null; priced: boolean; price_per_minute: number | null; note: string };
    daily: { date: string; llm_amount: number | null; browser_amount: number | null; total: number | null; tokens: number; browser_minutes: number }[];
    top_runs: {
        run_id: number; suite_name: string | null; project_id: number | null; project_name?: string | null;
        created_at: string | null; tokens: number; unpriced_tokens: number; browser_minutes: number;
        llm_amount: number | null; browser_amount: number | null; total: number | null;
    }[];
    projects: CostProjectRow[];
}

export interface CostBudget {
    project_id: number;
    monthly_amount: number | null;
    currency: string | null;
    updated_at: string | null;
    thresholds: number[];
}

export const costsApi = {
    report: async (workspaceId: number, days: number, projectId?: number | null): Promise<CostReport> =>
        (await api.get(`/costs/workspaces/${workspaceId}`, {
            params: { days, ...(projectId ? { project_id: projectId } : {}) },
        })).data,
    budget: async (projectId: number): Promise<CostBudget> => (await api.get(`/projects/${projectId}/cost-budget`)).data,
    setBudget: async (projectId: number, monthlyAmount: number | null): Promise<CostBudget> =>
        (await api.put(`/projects/${projectId}/cost-budget`, { monthly_amount: monthlyAmount })).data,
};
