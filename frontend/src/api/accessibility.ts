import { api } from '@/lib/api';
import type { FlakePerson } from '@/api/flakes';

// Mirrors backend/app/api/accessibility.py and app/services/accessibility.py.

export type Impact = 'minor' | 'moderate' | 'serious' | 'critical';
export const IMPACTS: Impact[] = ['critical', 'serious', 'moderate', 'minor'];

export interface A11yViolation {
    id: number;
    test_run_id: number;
    test_case_id: number | null;
    test_name: string | null;
    step_id: string | null;
    page_key: string | null;
    rule_id: string;
    impact: Impact;
    target: string;
    fingerprint: string;
    node_count: number;
    help_url: string | null;
    new: boolean;
    waived: boolean;
    waiver_id: number | null;
    last_seen?: string;
}

export interface A11yDay extends Record<Impact, number> {
    date: string;
    new: number;
    fixed: number;
    waived: number;
}

export interface A11yRule {
    rule_id: string;
    impact: Impact;
    help_url: string | null;
    open: number;
    waived: number;
    nodes: number;
}

export interface A11yTrend {
    project_id: number;
    days: number;
    checks: number;
    runs: number;
    series: A11yDay[];
    open: A11yViolation[];
    totals: Record<Impact, number> & { waived: number; new: number };
    rules: A11yRule[];
}

export type WaiverState = 'active' | 'expired';

export interface A11yWaiver {
    id: number;
    project_id: number;
    rule_id: string;
    selector_pattern: string;
    reason: string;
    owner: FlakePerson | null;
    created_by: FlakePerson | null;
    created_at: string;
    updated_at: string;
    expires_at: string;
    state: WaiverState;
}

export interface WaiverInput {
    rule_id: string;
    selector_pattern?: string;
    reason: string;
    days?: number;
    owner_user_id?: number | null;
}

export const accessibilityApi = {
    trend: async (projectId: number, days: number): Promise<A11yTrend> =>
        (await api.get(`/projects/${projectId}/accessibility?days=${days}`)).data,
    waivers: async (projectId: number): Promise<A11yWaiver[]> =>
        (await api.get(`/projects/${projectId}/accessibility/waivers`)).data,
    createWaiver: async (projectId: number, body: WaiverInput): Promise<A11yWaiver> =>
        (await api.post(`/projects/${projectId}/accessibility/waivers`, body)).data,
    renewWaiver: async (id: number, days: number): Promise<A11yWaiver> =>
        (await api.put(`/accessibility-waivers/${id}`, { days })).data,
    deleteWaiver: async (id: number): Promise<void> => {
        await api.delete(`/accessibility-waivers/${id}`);
    },
};
