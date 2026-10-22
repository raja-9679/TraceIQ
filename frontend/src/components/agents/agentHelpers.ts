import type { Tone } from '@/components/redesign/kinds';
import type { AgentControl, AgentControlInput, LimitField, Limits, PolicyRule, TimelineItem } from '@/api/policies';

// Pure helpers for the Policies and Agents pages (WP14b). Kept out of the
// component files so those export only components (fast-refresh lint).

export const WILDCARD = '*';

export const LIMIT_FIELDS: { key: LimitField; label: string; hint: string }[] = [
    { key: 'max_runs', label: 'Runs per session', hint: 'Runs one agent session may start.' },
    { key: 'max_llm_tokens', label: 'LLM tokens per session', hint: 'Tokens spent on LLM calls made for the session.' },
    { key: 'max_proposals', label: 'Proposals per session', hint: 'Case proposals one session may file.' },
    { key: 'max_minutes', label: 'Minutes per session', hint: 'Wall-clock time from the session’s first budgeted action.' },
];

/** Form state: '' = inherit, '0' = no limit, 'n' = cap. */
export type LimitDraft = Record<LimitField, string>;

export interface ControlDraft {
    agent_id: string;
    blocked: boolean;
    blocked_reason: string;
    limits: LimitDraft;
}

export function draftFrom(agentId: string, c: AgentControl | null | undefined): ControlDraft {
    const limits = {} as LimitDraft;
    for (const { key } of LIMIT_FIELDS) {
        const v = c?.[key];
        limits[key] = v === null || v === undefined ? '' : String(v);
    }
    return { agent_id: agentId, blocked: !!c?.blocked, blocked_reason: c?.blocked_reason ?? '', limits };
}

/** Parse one limit box. Returns undefined for invalid input (the form shows an error). */
export function parseLimit(raw: string): number | null | undefined {
    const t = raw.trim();
    if (t === '') return null;
    if (!/^\d+$/.test(t)) return undefined;
    const n = Number(t);
    return n <= 1_000_000_000 ? n : undefined;
}

export function draftErrors(d: ControlDraft): string[] {
    const errors: string[] = [];
    if (!d.agent_id.trim()) errors.push('Agent id is required');
    for (const { key, label } of LIMIT_FIELDS) {
        if (parseLimit(d.limits[key]) === undefined) errors.push(`${label}: a whole number, or empty to inherit`);
    }
    return errors;
}

export function draftToInput(d: ControlDraft): AgentControlInput {
    const out = {
        agent_id: d.agent_id.trim(),
        blocked: d.blocked,
        blocked_reason: d.blocked ? (d.blocked_reason.trim() || null) : null,
    } as AgentControlInput;
    for (const { key } of LIMIT_FIELDS) out[key] = parseLimit(d.limits[key]) ?? null;
    return out;
}

/** "12 / 50" or "12" when there is no cap. */
export function usageLabel(used: number, cap: number | undefined): string {
    return cap ? `${fmtCount(used)} / ${fmtCount(cap)}` : fmtCount(used);
}

export function fmtCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 10_000) return `${Math.round(n / 1000)}k`;
    return String(n);
}

export function limitSummary(l: Limits | undefined): string {
    if (!l) return 'No limits';
    const parts = LIMIT_FIELDS.filter(({ key }) => l[key]).map(({ key }) => {
        const n = l[key];
        return key === 'max_runs' ? `${n} runs` : key === 'max_llm_tokens' ? `${fmtCount(n)} tokens`
            : key === 'max_proposals' ? `${n} proposals` : `${n} min`;
    });
    return parts.length ? parts.join(' · ') : 'No limits';
}

export const KIND_META: Record<TimelineItem['kind'], { label: string; tone: Tone }> = {
    run_started: { label: 'Run', tone: 'lavender' },
    case_created: { label: 'Case created', tone: 'mint' },
    suite_created: { label: 'Suite created', tone: 'mint' },
    case_edited: { label: 'Case edited', tone: 'butter' },
    proposal_made: { label: 'Proposal', tone: 'pink' },
    proposal_decided: { label: 'Decision', tone: 'pink' },
    llm_call: { label: 'LLM', tone: 'neutral' },
    denied: { label: 'Refused', tone: 'peach' },
};

export const OUTCOME_META: Record<PolicyRule['outcome'], { label: string; tone: Tone }> = {
    deny: { label: 'Refuses', tone: 'peach' },
    defer: { label: 'Defers', tone: 'butter' },
    resolve: { label: 'Resolves settings', tone: 'lavender' },
};

export const ACTION_LABELS: Record<string, string> = {
    'run.dispatch': 'Starting and dispatching runs',
    'ai.use': 'Using AI (LLM calls)',
    'agent.write': 'Agent writes',
    'proposal.create': 'Filing proposals',
    'proposal.approve': 'Accepting proposals',
    'proposal.auto_apply': 'Auto-applying proposals',
    'security.scan': 'Security scans',
    'backend.fetch': 'Backend outbound requests',
};

/** Rules grouped by action, in a stable order. */
export function groupRules(rules: PolicyRule[]): { action: string; label: string; rules: PolicyRule[] }[] {
    const order = Object.keys(ACTION_LABELS);
    const groups = new Map<string, PolicyRule[]>();
    for (const r of rules) groups.set(r.action, [...(groups.get(r.action) ?? []), r]);
    return [...groups.entries()]
        .sort(([a], [b]) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99) || a.localeCompare(b))
        .map(([action, rs]) => ({ action, label: ACTION_LABELS[action] ?? action, rules: rs }));
}

export const EXHAUSTED_LABEL: Record<string, string> = { run: 'runs', proposal: 'proposals', llm: 'tokens' };

/** What an agent's empty limit box falls back to: the workspace "*" row, else the instance default. */
export function inheritedLimits(workspaceDefault: AgentControl | null | undefined, instance: Limits): Limits {
    const out = { ...instance };
    for (const { key } of LIMIT_FIELDS) {
        const v = workspaceDefault?.[key];
        if (v !== null && v !== undefined) out[key] = v;
    }
    return out;
}

/** Ids TraceIQ stamps on its own runs, not AI agents: scheduled runs carry
 *  `schedule:<id>` (schedule_tasks) and the demo seeder `demo-seed`. They are
 *  hidden from the Agents list unless someone has set controls for them. */
export function isPseudoAgent(agentId: string): boolean {
    return /^schedule:\d+$/.test(agentId) || agentId === 'demo-seed';
}

export function realAgents<T extends { agent_id: string; control: unknown }>(agents: T[]): T[] {
    return agents.filter((a) => !isPseudoAgent(a.agent_id) || a.control != null);
}
