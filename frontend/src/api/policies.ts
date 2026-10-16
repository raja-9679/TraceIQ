import { api } from '@/lib/api';

// Mirrors backend/app/api/policies.py and backend/app/api/agents.py (WP14b).

export interface PolicyRule {
    name: string;
    action: string;
    description: string;
    configured: string;
    stages: string[];
    enforced_by_engine: boolean;
    enforced_in: string;
    outcome: 'deny' | 'defer' | 'resolve';
}

export interface AiPolicy {
    workspace_id: number;
    ai_enabled: boolean;
    can_edit: boolean;
}

export interface PolicyDenial {
    id: number;
    created_at: string;
    action: string;
    rule: string;
    reason: string;
    workspace_id: number | null;
    project_id: number | null;
    project_name: string | null;
    user_id: number | null;
    user_email: string | null;
    api_key_id: number | null;
    agent_id: string | null;
    agent_session_id: string | null;
    data: Record<string, unknown>;
}

export interface DenialFilters {
    action?: string;
    rule?: string;
    agent_id?: string;
    days?: number;
    limit?: number;
    offset?: number;
}

export interface DenialPage {
    total: number;
    items: PolicyDenial[];
}

export type LimitField = 'max_runs' | 'max_llm_tokens' | 'max_proposals' | 'max_minutes';
export type Limits = Record<LimitField, number>;

export interface AgentControl {
    agent_id: string;
    blocked: boolean;
    blocked_reason: string | null;
    max_runs: number | null;
    max_llm_tokens: number | null;
    max_proposals: number | null;
    max_minutes: number | null;
    updated_at?: string;
    updated_by_id?: number | null;
}

export type AgentControlInput = Omit<AgentControl, 'updated_at' | 'updated_by_id'>;

export interface AgentSummary {
    agent_id: string;
    sessions: number;
    runs: number;
    proposals: number;
    llm_tokens: number;
    cases_created: number;
    denials: number;
    last_seen_at: string | null;
    control: AgentControl | null;
    effective_limits: Limits;
    blocked: boolean;
}

export interface AgentsOverview {
    workspace_id: number;
    agents: AgentSummary[];
    workspace_default: AgentControl | null;
    instance_defaults: Limits;
}

export interface AgentSessionRow {
    agent_id: string;
    agent_session_id: string;
    runs: number;
    proposals: number;
    llm_tokens: number;
    denials: number;
    first_seen_at: string;
    last_seen_at: string;
    limits: Limits;
    exhausted: ('run' | 'proposal' | 'llm')[];
}

export interface TimelineItem {
    kind: 'run_started' | 'case_created' | 'suite_created' | 'case_edited' | 'proposal_made'
        | 'proposal_decided' | 'llm_call' | 'denied';
    at: string;
    title: string;
    entity_type: string;
    entity_id: number;
    project_id: number | null;
    link: string | null;
    status?: string;
}

export interface SessionTimeline {
    agent_id: string;
    agent_session_id: string;
    session: Omit<AgentSessionRow, 'agent_id' | 'agent_session_id' | 'limits' | 'exhausted'> | null;
    items: TimelineItem[];
}

const clean = (f: DenialFilters) =>
    Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== ''));

export const policiesApi = {
    rules: () => api.get<PolicyRule[]>('/policy/rules').then((r) => r.data),
    aiPolicy: (ws: number) => api.get<AiPolicy>(`/workspaces/${ws}/ai-policy`).then((r) => r.data),
    setAiPolicy: (ws: number, ai_enabled: boolean) =>
        api.put<AiPolicy>(`/workspaces/${ws}/ai-policy`, { ai_enabled }).then((r) => r.data),
    denials: (ws: number, f: DenialFilters) =>
        api.get<DenialPage>(`/workspaces/${ws}/policy-decisions`, { params: clean(f) }).then((r) => r.data),
    instanceDenials: (f: DenialFilters) =>
        api.get<DenialPage>('/admin/policy-decisions', { params: clean(f) }).then((r) => r.data),
};

export const agentsApi = {
    overview: (ws: number) => api.get<AgentsOverview>(`/workspaces/${ws}/agents`).then((r) => r.data),
    sessions: (ws: number, agentId?: string) =>
        api.get<{ sessions: AgentSessionRow[] }>(`/workspaces/${ws}/agents/sessions`,
            { params: agentId ? { agent_id: agentId } : {} }).then((r) => r.data.sessions),
    timeline: (ws: number, agentId: string, sessionId: string) =>
        api.get<SessionTimeline>(`/workspaces/${ws}/agents/timeline`,
            { params: { agent_id: agentId, agent_session_id: sessionId } }).then((r) => r.data),
    saveControl: (ws: number, body: AgentControlInput) =>
        api.put<AgentControl>(`/workspaces/${ws}/agent-controls`, body).then((r) => r.data),
    removeControl: (ws: number, agentId: string) =>
        api.delete(`/workspaces/${ws}/agent-controls`, { params: { agent_id: agentId } }).then((r) => r.data),
};
