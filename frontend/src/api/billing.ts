import { api } from '@/lib/api';

// Mirrors backend/app/api/billing.py

export interface Plan {
    id: number;
    name: string;
    display_name: string;
    price_cents: number;
    limits: Record<string, number>;
    is_active: boolean;
}

export interface BillingStatus {
    workspace_id: number;
    plan: Plan;
    status: string;
    period: string;
    usage: Record<string, number>;
    limits: Record<string, number>;
    current_period_end?: string | null;
    stripe_configured: boolean;
}

export const billingApi = {
    plans: async (): Promise<Plan[]> => (await api.get('/plans')).data,
    status: async (workspaceId: number): Promise<BillingStatus> =>
        (await api.get(`/workspaces/${workspaceId}/billing`)).data,
    assignPlan: async (workspaceId: number, planName: string): Promise<BillingStatus> =>
        (await api.post(`/workspaces/${workspaceId}/subscription`, { plan_name: planName })).data,
    checkout: async (workspaceId: number, planName: string): Promise<{ checkout_url: string }> =>
        (await api.post('/billing/stripe/checkout', { workspace_id: workspaceId, plan_name: planName })).data,
};
