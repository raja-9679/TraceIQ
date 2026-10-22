import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, ListChecks, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, SettingsCard, inputClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { selectClass } from '@/components/compliance/selectClass';
import { fmtDate } from '@/components/compliance/helpers';
import { errDetail } from '@/lib/errors';
import { policiesApi, type AiPolicy, type DenialFilters, type PolicyRule } from '@/api/policies';
import { ACTION_LABELS, OUTCOME_META, groupRules } from './agentHelpers';
import { useIsInstanceAdmin } from '@/hooks/useIsInstanceAdmin';

export function AiPolicyCard({ policy }: { policy: AiPolicy }) {
    const qc = useQueryClient();
    const [enabled, setEnabled] = useState(policy.ai_enabled);
    const save = useMutation({
        mutationFn: () => policiesApi.setAiPolicy(policy.workspace_id, enabled),
        onSuccess: (p) => { qc.setQueryData(['ai-policy', policy.workspace_id], p); toast.success('Saved'); },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="ai-policy"
            icon={<Bot className="h-4 w-4" />}
            title="AI use in this workspace"
            description="When off, nothing in this workspace calls an LLM: no failure analysis, case generation, selector heal (on the server or on workers) and no assistant. Features fall back to their non-AI behaviour."
            actions={<Pill tone={policy.ai_enabled ? 'mint' : 'peach'}>{policy.ai_enabled ? 'AI on' : 'AI off'}</Pill>}
            footer={policy.can_edit ? (
                <Button onClick={() => save.mutate()} disabled={save.isPending || enabled === policy.ai_enabled}>Save</Button>
            ) : <span className="text-xs text-ink-soft">Only workspace admins can change this.</span>}
        >
            <CheckRow id="ai-enabled" label="Allow AI features" checked={enabled} onChange={setEnabled}
                disabled={!policy.can_edit}
                hint="On by default. The plan's monthly token quota and per-agent budgets still apply when on." />
        </SettingsCard>
    );
}

function RuleRow({ rule }: { rule: PolicyRule }) {
    const outcome = OUTCOME_META[rule.outcome] ?? OUTCOME_META.deny;
    return (
        <li className="flex flex-col gap-1 border-t border-[#F0EEE8] py-3 first:border-t-0">
            <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-[13px] font-semibold text-ink">{rule.name}</span>
                <Pill tone={outcome.tone}>{outcome.label}</Pill>
                {!rule.enforced_by_engine && <Pill tone="neutral">Enforced inline</Pill>}
                {rule.stages.length > 0 && <span className="text-xs text-ink-soft">at {rule.stages.join(', ')}</span>}
            </div>
            <p className="text-sm text-ink">{rule.description}</p>
            <p className="text-xs text-ink-soft"><span className="font-medium">Configured in:</span> {rule.configured}</p>
            {rule.enforced_in && <p className="font-mono text-[11px] text-ink-soft">{rule.enforced_in}</p>}
        </li>
    );
}

export function RulesCard() {
    const rules = useQuery({ queryKey: ['policy-rules'], queryFn: policiesApi.rules, staleTime: 5 * 60_000 });
    return (
        <SettingsCard
            labelledBy="policy-rules"
            icon={<ListChecks className="h-4 w-4" />}
            title="Rules"
            description="Every check the policy engine evaluates, grouped by what it guards. Each refusal below names the rule that fired."
        >
            {rules.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : rules.isError ? (
                <p className="text-sm text-ink-soft">{errDetail(rules.error)}</p>
            ) : (
                <div className="flex flex-col gap-4">
                    {groupRules(rules.data ?? []).map((g) => (
                        <div key={g.action} className="flex flex-col">
                            <h3 className="text-sm font-semibold text-ink">{g.label} <span className="font-mono text-xs font-normal text-ink-soft">{g.action}</span></h3>
                            <ul className="flex flex-col">{g.rules.map((r) => <RuleRow key={`${r.action}:${r.name}`} rule={r} />)}</ul>
                        </div>
                    ))}
                </div>
            )}
        </SettingsCard>
    );
}

const PAGE = 50;

export function DenialsCard({ workspaceId }: { workspaceId: number }) {
    const [scope, setScope] = useState<'workspace' | 'instance'>('workspace');
    // Only offered to instance admins: for anyone else the shared guard's 403
    // talks about instance settings, which reads like a different page broke.
    const isInstanceAdmin = useIsInstanceAdmin();
    const [action, setAction] = useState('');
    const [rule, setRule] = useState('');
    const [agent, setAgent] = useState('');
    const [days, setDays] = useState(30);
    const [offset, setOffset] = useState(0);
    const rules = useQuery({ queryKey: ['policy-rules'], queryFn: policiesApi.rules, staleTime: 5 * 60_000 });
    const filters: DenialFilters = { action, rule, agent_id: agent.trim(), days, limit: PAGE, offset };
    const page = useQuery({
        queryKey: ['policy-denials', scope, workspaceId, filters],
        queryFn: () => (scope === 'instance' ? policiesApi.instanceDenials(filters) : policiesApi.denials(workspaceId, filters)),
        retry: false,
    });
    const ruleNames = [...new Set((rules.data ?? []).filter((r) => !action || r.action === action).map((r) => r.name))].sort();
    const reset = () => setOffset(0);
    const total = page.data?.total ?? 0;
    return (
        <SettingsCard
            labelledBy="policy-denials"
            icon={<ShieldAlert className="h-4 w-4" />}
            title="Recent denials"
            description="Refused actions, newest first, kept 90 days by default. Allowed actions are not logged."
        >
            <div className="flex flex-wrap items-end gap-3">
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Scope
                    <select className={selectClass} value={scope} onChange={(e) => { setScope(e.target.value as 'workspace' | 'instance'); reset(); }}>
                        <option value="workspace">This workspace</option>
                        {isInstanceAdmin && <option value="instance">Whole instance</option>}
                    </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Action
                    <select className={selectClass} value={action} onChange={(e) => { setAction(e.target.value); setRule(''); reset(); }}>
                        <option value="">Any</option>
                        {Object.entries(ACTION_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Rule
                    <select className={selectClass} value={rule} onChange={(e) => { setRule(e.target.value); reset(); }}>
                        <option value="">Any</option>
                        {ruleNames.map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Agent id
                    <Input className={`${inputClass} w-44`} value={agent} placeholder="any"
                        onChange={(e) => { setAgent(e.target.value); reset(); }} />
                </label>
                <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Period
                    <select className={selectClass} value={days} onChange={(e) => { setDays(Number(e.target.value)); reset(); }}>
                        <option value={1}>Last 24 hours</option>
                        <option value={7}>Last 7 days</option>
                        <option value={30}>Last 30 days</option>
                        <option value={90}>Last 90 days</option>
                    </select>
                </label>
            </div>
            {page.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : page.isError ? (
                <p className={warnCalloutClass}>{errDetail(page.error)}</p>
            ) : total === 0 ? (
                <p className="text-sm text-ink-soft">Nothing was refused in this period.</p>
            ) : (
                // A stacked list, not a five-column table: this card sits in
                // half the page width, where the table squeezed the reason
                // column to one word per line.
                <ul aria-label="Refused actions" className="flex flex-col">
                    {page.data!.items.map((d) => {
                        const where = d.project_name ?? (d.project_id ? `project ${d.project_id}` : null);
                        const who = d.user_email ?? (d.user_id ? `user ${d.user_id}` : 'system');
                        return (
                            <li key={d.id} className="flex flex-col gap-1 border-t border-border py-3 first:border-t-0">
                                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                                    <span className="min-w-0 font-mono text-xs font-semibold text-ink [overflow-wrap:anywhere]">
                                        {d.rule} <span className="font-sans font-normal text-ink-soft">· {d.action}</span>
                                    </span>
                                    <span className="whitespace-nowrap text-xs text-ink-soft">{fmtDate(d.created_at)}</span>
                                </div>
                                <p className="text-sm text-ink">{d.reason}</p>
                                <dl className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-ink-soft">
                                    <div className="flex min-w-0 gap-1"><dt>Where:</dt><dd className="text-ink">{where ?? '—'}{scope === 'instance' && d.workspace_id ? ` · workspace ${d.workspace_id}` : ''}</dd></div>
                                    <div className="flex min-w-0 gap-1"><dt>Who:</dt><dd className="min-w-0 text-ink [overflow-wrap:anywhere]">
                                        {d.agent_id ? <span className="font-medium">{d.agent_id}</span> : null}
                                        {d.agent_session_id ? <span className="font-mono text-ink-soft"> {d.agent_session_id}</span> : null}
                                        {d.agent_id || d.agent_session_id ? <span className="text-ink-soft"> for {who}</span> : who}
                                    </dd></div>
                                </dl>
                            </li>
                        );
                    })}
                </ul>
            )}
            {total > PAGE && (
                <div className="flex items-center gap-3">
                    <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</Button>
                    <span className="text-xs text-ink-soft">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
                    <Button variant="outline" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Older</Button>
                </div>
            )}
        </SettingsCard>
    );
}
