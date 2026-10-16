import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Bot, Clock, Gauge, History } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, SettingsCard, inputClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { fmtDate } from '@/components/compliance/helpers';
import { errDetail } from '@/lib/errors';
import { agentsApi, type AgentControl, type AgentSummary, type Limits } from '@/api/policies';
import {
    EXHAUSTED_LABEL, KIND_META, LIMIT_FIELDS, WILDCARD, draftErrors, draftFrom, draftToInput,
    fmtCount, limitSummary, usageLabel, type ControlDraft,
} from './agentHelpers';

export function AgentList({ agents, selected, onSelect }: {
    agents: AgentSummary[];
    selected: string | null;
    onSelect: (agentId: string) => void;
}) {
    return (
        <SettingsCard
            labelledBy="agents-list"
            icon={<Bot className="h-4 w-4" />}
            title="Agents"
            description="Every agent id (X-Agent-Id) seen in this workspace, from runs, cases, proposals and budget sessions."
        >
            {agents.length === 0 ? (
                <p className="text-sm text-ink-soft">No agent has worked in this workspace yet. Agents identify themselves with the X-Agent-Id and X-Agent-Session-Id headers.</p>
            ) : (
                <ul className="flex flex-col">
                    {agents.map((a) => (
                        <li key={a.agent_id} className="flex flex-wrap items-center justify-between gap-3 border-t border-[#F0EEE8] py-3 first:border-t-0">
                            <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="font-mono text-sm font-semibold text-ink">{a.agent_id}</span>
                                    {a.blocked && <Pill tone="peach">Blocked</Pill>}
                                    {a.denials > 0 && <Pill tone="butter">{a.denials} refused</Pill>}
                                </div>
                                <p className="mt-0.5 text-xs text-ink-soft">
                                    {a.sessions} session{a.sessions === 1 ? '' : 's'} · {a.runs} runs · {a.cases_created} cases · {a.proposals} proposals · {fmtCount(a.llm_tokens)} tokens
                                </p>
                                <p className="text-xs text-ink-soft">Last seen {fmtDate(a.last_seen_at)} · {limitSummary(a.effective_limits)}</p>
                            </div>
                            <Button variant={selected === a.agent_id ? 'default' : 'outline'} onClick={() => onSelect(a.agent_id)}>
                                {selected === a.agent_id ? 'Selected' : 'Open'}
                            </Button>
                        </li>
                    ))}
                </ul>
            )}
        </SettingsCard>
    );
}

export function ControlForm({ workspaceId, agentId, control, inherited }: {
    workspaceId: number;
    agentId: string;
    control: AgentControl | null;
    /** What an empty box falls back to (workspace default, else instance). */
    inherited: Limits;
}) {
    const qc = useQueryClient();
    const wildcard = agentId === WILDCARD;
    const [draft, setDraft] = useState<ControlDraft>(() => draftFrom(agentId, control));
    const errors = draftErrors(draft);
    const done = (msg: string) => {
        qc.invalidateQueries({ queryKey: ['agents', workspaceId] });
        toast.success(msg);
    };
    const save = useMutation({
        mutationFn: () => agentsApi.saveControl(workspaceId, draftToInput(draft)),
        onSuccess: () => done('Saved'),
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => agentsApi.removeControl(workspaceId, agentId),
        onSuccess: () => { setDraft(draftFrom(agentId, null)); done('Controls removed'); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const setLimit = (key: keyof ControlDraft['limits'], v: string) =>
        setDraft((d) => ({ ...d, limits: { ...d.limits, [key]: v } }));
    return (
        <SettingsCard
            labelledBy={`controls-${wildcard ? 'default' : 'agent'}`}
            icon={<Gauge className="h-4 w-4" />}
            title={wildcard ? 'Defaults for every agent' : <>Controls for <span className="font-mono">{agentId}</span></>}
            description={wildcard
                ? 'Applies to agents without their own controls. Blocking here blocks every agent in this workspace.'
                : 'Budgets apply to each session of this agent. Leave a box empty to inherit; 0 means no limit.'}
            actions={control?.blocked ? <Pill tone="peach">Blocked</Pill> : undefined}
            footer={<>
                <Button onClick={() => save.mutate()} disabled={save.isPending || errors.length > 0}>Save</Button>
                {control && <Button variant="outline" onClick={() => remove.mutate()} disabled={remove.isPending}>Remove controls</Button>}
            </>}
        >
            <CheckRow id={`block-${agentId}`} label={wildcard ? 'Block all agents (kill switch)' : 'Block this agent (kill switch)'}
                hint="Blocked agents get 403 on every write in this workspace; reads keep working."
                checked={draft.blocked} onChange={(v) => setDraft((d) => ({ ...d, blocked: v }))} />
            {draft.blocked && (
                <label className="flex flex-col gap-1 text-sm font-medium text-ink">Reason (shown to the agent)
                    <Input className={inputClass} value={draft.blocked_reason} maxLength={500}
                        onChange={(e) => setDraft((d) => ({ ...d, blocked_reason: e.target.value }))} />
                </label>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {LIMIT_FIELDS.map(({ key, label, hint }) => (
                    <label key={key} className="flex flex-col gap-1 text-sm font-medium text-ink">
                        {label}
                        <Input className={inputClass} inputMode="numeric" value={draft.limits[key]}
                            placeholder={inherited[key] ? `inherits ${inherited[key]}` : 'inherits: no limit'}
                            onChange={(e) => setLimit(key, e.target.value)} />
                        <span className="text-xs font-normal text-ink-soft">{hint}</span>
                    </label>
                ))}
            </div>
            {errors.length > 0 && <p className={warnCalloutClass}>{errors.join('. ')}</p>}
        </SettingsCard>
    );
}

export function SessionsCard({ workspaceId, agentId, selected, onSelect }: {
    workspaceId: number;
    agentId: string;
    selected: string | null;
    onSelect: (sessionId: string) => void;
}) {
    const sessions = useQuery({
        queryKey: ['agents', workspaceId, 'sessions', agentId],
        queryFn: () => agentsApi.sessions(workspaceId, agentId),
    });
    return (
        <SettingsCard
            labelledBy="agent-sessions"
            icon={<Clock className="h-4 w-4" />}
            title="Sessions"
            description="One row per X-Agent-Session-Id: usage against the session's budget."
        >
            {sessions.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : sessions.isError ? (
                <p className={warnCalloutClass}>{errDetail(sessions.error)}</p>
            ) : !sessions.data?.length ? (
                <p className="text-sm text-ink-soft">No sessions recorded. Sessions appear once the agent starts a run, files a proposal or uses AI with a session header.</p>
            ) : (
                <ul className="flex flex-col">
                    {sessions.data.map((s) => (
                        <li key={s.agent_session_id} className="flex flex-wrap items-center justify-between gap-3 border-t border-[#F0EEE8] py-3 first:border-t-0">
                            <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="break-all font-mono text-xs font-semibold text-ink">{s.agent_session_id}</span>
                                    {s.exhausted.map((k) => <Pill key={k} tone="peach">Out of {EXHAUSTED_LABEL[k] ?? k}</Pill>)}
                                </div>
                                <p className="mt-0.5 text-xs text-ink-soft">
                                    Runs {usageLabel(s.runs, s.limits.max_runs)} · proposals {usageLabel(s.proposals, s.limits.max_proposals)} · tokens {usageLabel(s.llm_tokens, s.limits.max_llm_tokens)}
                                    {s.denials > 0 && ` · ${s.denials} refused`}
                                </p>
                                <p className="text-xs text-ink-soft">{fmtDate(s.first_seen_at)} → {fmtDate(s.last_seen_at)}</p>
                            </div>
                            <Button variant={selected === s.agent_session_id ? 'default' : 'outline'} onClick={() => onSelect(s.agent_session_id)}>
                                {selected === s.agent_session_id ? 'Showing timeline' : 'Show timeline'}
                            </Button>
                        </li>
                    ))}
                </ul>
            )}
        </SettingsCard>
    );
}

export function TimelineCard({ workspaceId, agentId, sessionId }: { workspaceId: number; agentId: string; sessionId: string }) {
    const tl = useQuery({
        queryKey: ['agents', workspaceId, 'timeline', agentId, sessionId],
        queryFn: () => agentsApi.timeline(workspaceId, agentId, sessionId),
    });
    return (
        <SettingsCard
            labelledBy="agent-timeline"
            icon={<History className="h-4 w-4" />}
            title="Session timeline"
            description={<>What <span className="font-mono">{agentId}</span> did in session <span className="break-all font-mono">{sessionId}</span>, newest first.</>}
        >
            {tl.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : tl.isError ? (
                <p className={warnCalloutClass}>{errDetail(tl.error)}</p>
            ) : !tl.data?.items.length ? (
                <p className="text-sm text-ink-soft">Nothing recorded for this session.</p>
            ) : (
                <ol className="flex flex-col">
                    {tl.data.items.map((i, idx) => {
                        const meta = KIND_META[i.kind] ?? { label: i.kind, tone: 'neutral' as const };
                        return (
                            <li key={`${i.kind}-${i.entity_id}-${idx}`} className="flex flex-wrap items-start gap-3 border-t border-[#F0EEE8] py-2.5 first:border-t-0">
                                <span className="w-36 flex-none text-xs text-ink-soft">{fmtDate(i.at)}</span>
                                <Pill tone={meta.tone}>{meta.label}</Pill>
                                <span className="min-w-0 flex-1 text-sm text-ink">
                                    {i.link ? <Link className="underline-offset-2 hover:underline" to={i.link}>{i.title}</Link> : i.title}
                                    {i.status && <span className="ml-2 text-xs text-ink-soft">{i.status}</span>}
                                </span>
                            </li>
                        );
                    })}
                </ol>
            )}
        </SettingsCard>
    );
}
