import { useState, useMemo } from 'react';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNowStrict } from 'date-fns';
import { Bot, Check, MoveRight, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { toast } from 'sonner';
import {
    caseProposalsApi, healProposalsApi, getTestCaseInfo,
    CaseProposal, HealProposal, HealRecheck, ProposedStep, TestCaseInfo,
} from '@/api/proposals';
import HealCandidateList from '@/components/heal/HealCandidateList';
import ValidationPanel from '@/components/proposals/ValidationPanel';
import { acceptBlock, anyValidating, caseValidatable, detailText } from '@/components/proposals/repairValidation';
import { candidatesOf, defaultPick } from '@/components/heal/healCandidates';
import { api, getProjects } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    DetailCard, EmptyState, EvidenceList, Eyebrow, KindChip, ListGroup, ListRow, PageHeader, Panel, PanelTabs, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Auto-apply policy — workspace-level threshold above which agent CREATE /
// UPDATE proposals merge without waiting in this queue.
// ---------------------------------------------------------------------------

function AutoApplyPolicyCard({ projectId }: { projectId: number }) {
    const queryClient = useQueryClient();
    const [editing, setEditing] = useState(false);
    const [value, setValue] = useState('');

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const workspaceId = projects?.find((p) => p.id === projectId)?.workspace_id;

    const { data: policy } = useQuery({
        queryKey: ['proposal-policy', workspaceId],
        queryFn: async () => (await api.get(`/workspaces/${workspaceId}/proposal-policy`)).data,
        enabled: !!workspaceId,
    });

    const saveMutation = useMutation({
        // Only the fields being changed are sent: omitting a field leaves it alone
        // server-side, so editing the threshold cannot silently switch the
        // separate-approver control off.
        mutationFn: async (patch: {
            auto_apply_threshold?: number | null; require_separate_approver?: boolean;
            require_validation_before_accept?: boolean;
        }) =>
            (await api.put(`/workspaces/${workspaceId}/proposal-policy`, patch)).data,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['proposal-policy', workspaceId] });
            setEditing(false);
            toast.success('Proposal policy updated');
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onError: (err: any) => toast.error('Failed to update policy', {
            description: detailText(err.response?.data?.detail),
        }),
    });

    if (!workspaceId) return null;
    const threshold: number | null = policy?.auto_apply_threshold ?? null;
    const autoApplyOff = policy?.auto_apply_disabled_by_instance === true;
    const enabled = !!threshold && threshold > 0 && !autoApplyOff;
    // The instance policy is a floor the workspace cannot lower, so the stored
    // flag and the effective one can differ.
    const separationStored = policy?.require_separate_approver === true;
    const separationEnforced = policy?.separation_enforced === true;
    const forcedByInstance = separationEnforced && !separationStored;
    // WP16c: same floor pattern for "validate before accept".
    const validationStored = policy?.require_validation_before_accept === true;
    const validationEnforced = policy?.validation_enforced === true;
    const validationForced = validationEnforced && !validationStored;

    return (
        <section aria-label="Review policy" className="flex flex-col gap-3 rounded-2xl border border-border bg-white px-5 py-4 lg:flex-row lg:items-center lg:gap-6">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-2">
                <span className="text-sm font-semibold text-ink">Auto-apply</span>
                {editing ? (
                    <>
                        <label className="flex items-center gap-2 text-[13px] text-ink-soft">
                            when confidence is at least
                            <Input
                                autoFocus
                                value={value}
                                onChange={(e) => setValue(e.target.value)}
                                placeholder="0.95"
                                aria-label="Auto-apply confidence threshold, 0 to 1"
                                className="h-10 w-24 rounded-full font-mono text-sm"
                            />
                        </label>
                        <Button size="sm" className="h-10 px-4" disabled={saveMutation.isPending}
                            onClick={() => {
                                const n = parseFloat(value);
                                if (value.trim() !== '' && (isNaN(n) || n < 0 || n > 1)) {
                                    toast.error('Threshold must be between 0 and 1 (empty = off)');
                                    return;
                                }
                                saveMutation.mutate({ auto_apply_threshold: value.trim() === '' || n === 0 ? null : n });
                            }}>
                            Save
                        </Button>
                        <Button size="sm" variant="ghost" className="h-10 px-4" onClick={() => setEditing(false)}>Cancel</Button>
                    </>
                ) : (
                    <>
                        {enabled ? (
                            <Pill tone="butter"><span className="font-mono">confidence ≥ {threshold!.toFixed(2)}</span></Pill>
                        ) : autoApplyOff ? (
                            <span className="text-[13px] text-ink-soft">Turned off for the whole instance by an administrator</span>
                        ) : (
                            <span className="text-[13px] text-ink-soft">Off. Every proposal waits here for a person.</span>
                        )}
                        <span className="text-[13px] text-ink-soft">Creates and updates only; every change is snapshotted.</span>
                        <Button
                            size="sm"
                            variant="outline"
                            className="h-10 px-4"
                            onClick={() => { setValue(threshold ? String(threshold) : ''); setEditing(true); }}
                        >
                            Edit
                        </Button>
                    </>
                )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border pt-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
                <span className="text-sm font-semibold text-ink">Second approver</span>
                {separationEnforced ? (
                    <Pill tone="mint">Required</Pill>
                ) : (
                    <span className="text-[13px] text-ink-soft">Not required</span>
                )}
                {forcedByInstance && (
                    <span className="text-[13px] text-ink-soft">Enforced for the whole instance</span>
                )}
                {!forcedByInstance && (
                    <Button
                        size="sm"
                        variant="outline"
                        className="h-10 px-4"
                        onClick={() => saveMutation.mutate({ require_separate_approver: !separationStored })}
                        disabled={saveMutation.isPending}
                    >
                        {separationStored ? 'Allow self-approval' : 'Require a second person'}
                    </Button>
                )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-border pt-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
                <span className="text-sm font-semibold text-ink">Validate before accept</span>
                {validationEnforced ? (
                    <Pill tone="mint">Required</Pill>
                ) : (
                    <span className="text-[13px] text-ink-soft">Optional (protected suites always)</span>
                )}
                {validationForced && (
                    <span className="text-[13px] text-ink-soft">Enforced for the whole instance</span>
                )}
                {!validationForced && (
                    <Button
                        size="sm"
                        variant="outline"
                        className="h-10 px-4"
                        onClick={() => saveMutation.mutate({ require_validation_before_accept: !validationStored })}
                        disabled={saveMutation.isPending}
                    >
                        {validationStored ? 'Make validation optional' : 'Require a validation run'}
                    </Button>
                )}
            </div>
        </section>
    );
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const formatDate = (dateString?: string | null) => {
    if (!dateString) return '—';
    return new Date(dateString).toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
};
const ago = (iso: string | null | undefined) => (iso ? formatDistanceToNowStrict(new Date(iso), { addSuffix: true }) : '—');

const stepSignature = (s: ProposedStep) =>
    [s.type, s.selector || '', s.value || '', s.intent || ''].join('');

const describeStep = (s: ProposedStep) => {
    const parts: string[] = [s.type];
    if (s.selector) parts.push(s.selector);
    if (s.value) parts.push(`"${s.value}"`);
    return parts.join('  ');
};

interface DiffRow {
    kind: 'same' | 'changed' | 'added' | 'removed';
    current: ProposedStep | null;
    proposed: ProposedStep | null;
}

/** Align current vs proposed steps — by step id when available, else by index. */
function diffSteps(current: ProposedStep[], proposed: ProposedStep[]): DiffRow[] {
    const rows: DiffRow[] = [];
    const proposedById = new Map<string, number>();
    proposed.forEach((s, i) => { if (s.id) proposedById.set(s.id, i); });
    const usedProposed = new Set<number>();

    current.forEach((cur, idx) => {
        let matchIdx: number | undefined;
        if (cur.id && proposedById.has(cur.id)) {
            matchIdx = proposedById.get(cur.id);
        } else if (!cur.id && idx < proposed.length && !usedProposed.has(idx) && !proposed[idx].id) {
            matchIdx = idx;
        }
        if (matchIdx !== undefined && !usedProposed.has(matchIdx)) {
            usedProposed.add(matchIdx);
            const prop = proposed[matchIdx];
            rows.push({
                kind: stepSignature(cur) === stepSignature(prop) ? 'same' : 'changed',
                current: cur,
                proposed: prop,
            });
        } else {
            rows.push({ kind: 'removed', current: cur, proposed: null });
        }
    });
    proposed.forEach((prop, i) => {
        if (!usedProposed.has(i)) rows.push({ kind: 'added', current: null, proposed: prop });
    });
    return rows;
}

const ACTION_STYLES: Record<string, { label: string; tone: Tone; verb: string }> = {
    create: { label: 'Create', tone: 'mint', verb: 'add a new test' },
    update: { label: 'Update', tone: 'lavender', verb: 'change an existing test' },
    delete: { label: 'Delete', tone: 'peach', verb: 'delete a test' },
    move: { label: 'Move', tone: 'butter', verb: 'move a test to another suite' },
};
// Risk first: a delete is the change most worth a second look.
const ACTION_ORDER = ['delete', 'update', 'create', 'move'];

const STATUS_STYLES: Record<string, { label: string; tone: Tone }> = {
    pending: { label: 'Waiting for review', tone: 'butter' },
    accepted: { label: 'Accepted', tone: 'mint' },
    rejected: { label: 'Rejected', tone: 'neutral' },
    auto_applied: { label: 'Auto-applied', tone: 'lavender' },
};
const statusStyle = (s: string) => STATUS_STYLES[s] ?? { label: s, tone: 'neutral' as Tone };

function ConfidenceBadge({ value }: { value: number }) {
    const pct = Math.round(value * 100);
    const tone: Tone = value >= 0.8 ? 'mint' : value >= 0.5 ? 'butter' : 'neutral';
    return <Pill tone={tone}>{pct}% confidence</Pill>;
}

function CodePaths({ paths, title }: { paths: string[]; title: string }) {
    return (
        <div className="flex flex-col gap-2">
            <Eyebrow>{title}</Eyebrow>
            <div className="flex flex-wrap gap-1.5">
                {paths.map((p) => (
                    <code key={p} className="rounded-full bg-background px-2.5 py-1 font-mono text-xs text-ink">{p}</code>
                ))}
            </div>
        </div>
    );
}

function StepList({ steps, title }: { steps: ProposedStep[]; title?: string }) {
    if (!steps.length) {
        return <p className="text-sm text-ink-soft">No steps</p>;
    }
    return (
        <div className="flex flex-col gap-2">
            {title && <Eyebrow>{title}</Eyebrow>}
            <ol className="overflow-hidden rounded-2xl border border-border">
                {steps.map((s, i) => (
                    <li key={s.id || i} className={cn('flex items-start gap-3 px-4 py-3 text-sm', i > 0 && 'border-t border-[#F0EEE8]')}>
                        <span className="mt-0.5 flex h-6 w-6 flex-none items-center justify-center rounded-full bg-secondary text-xs font-semibold text-ink-soft">
                            {i + 1}
                        </span>
                        <div className="min-w-0">
                            <span className="mr-2 rounded-md bg-pastel-lavender px-1.5 py-0.5 font-mono text-xs font-semibold text-pastel-ink-lavender">{s.type}</span>
                            {s.selector && <code className="break-all font-mono text-xs text-ink">{s.selector}</code>}
                            {s.value && <span className="ml-1 text-xs text-ink-soft">= "{s.value}"</span>}
                            {s.intent && <p className="mt-0.5 text-xs text-ink-soft">{s.intent}</p>}
                        </div>
                    </li>
                ))}
            </ol>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Proposal detail (per action type)
// ---------------------------------------------------------------------------

function DiffCell({ step, kind, side }: { step: ProposedStep | null; kind: DiffRow['kind']; side: 'current' | 'proposed' }) {
    if (!step) return <div className="h-full bg-background/60 px-3 py-2 text-xs text-ink-muted">—</div>;
    const highlight =
        kind === 'same' ? 'bg-white' :
            kind === 'changed' ? (side === 'current' ? 'bg-pastel-peach/70' : 'bg-pastel-mint/70') :
                kind === 'added' ? 'bg-pastel-mint/70' : 'bg-pastel-peach/70';
    return (
        <div className={cn('h-full break-all px-3 py-2 font-mono text-xs text-ink', highlight)}>
            {describeStep(step)}
        </div>
    );
}

function UpdateDiff({ proposal }: { proposal: CaseProposal }) {
    const { data: currentCase, isLoading, error } = useQuery<TestCaseInfo>({
        queryKey: ['case', proposal.target_case_id],
        queryFn: () => getTestCaseInfo(proposal.target_case_id!),
        enabled: !!proposal.target_case_id,
    });

    if (isLoading) return <div className="h-24 animate-pulse rounded-2xl bg-background" aria-busy="true" aria-label="Loading current case" />;
    if (error || !currentCase) return <p className="text-sm text-pastel-ink-peach">Could not load current case #{proposal.target_case_id} for diffing.</p>;

    const proposedSteps = proposal.payload?.steps;
    const proposedName = proposal.payload?.name;
    const rows = proposedSteps ? diffSteps(currentCase.steps || [], proposedSteps) : [];

    return (
        <div className="flex flex-col gap-4">
            {proposedName && proposedName !== currentCase.name && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Rename</Eyebrow>
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="text-ink-soft line-through">{currentCase.name}</span>
                        <MoveRight className="h-3.5 w-3.5 text-ink-soft" aria-label="becomes" />
                        <span className="font-semibold text-ink">{proposedName}</span>
                    </div>
                </div>
            )}
            {proposedSteps ? (
                <div className="flex flex-col gap-2">
                    <Eyebrow right={<span className="flex items-center gap-3"><span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#E0603A]" />removed or before</span><span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-[#8FD3B6]" />added or after</span></span>}>
                        Step changes
                    </Eyebrow>
                    <div className="overflow-hidden rounded-2xl border border-border">
                        <div className="grid grid-cols-2 border-b border-border bg-background">
                            <div className="px-3 py-2 text-xs font-semibold text-ink-soft">Current steps ({currentCase.steps?.length || 0})</div>
                            <div className="border-l border-border px-3 py-2 text-xs font-semibold text-ink-soft">Proposed steps ({proposedSteps.length})</div>
                        </div>
                        {rows.map((row, i) => (
                            <div key={i} className="grid grid-cols-2 border-b border-[#F0EEE8] last:border-b-0">
                                <DiffCell step={row.current} kind={row.kind} side="current" />
                                <div className="border-l border-[#F0EEE8]">
                                    <DiffCell step={row.proposed} kind={row.kind} side="proposed" />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            ) : (
                <p className="text-sm text-ink-soft">No step changes proposed (metadata-only update).</p>
            )}
            {proposal.payload?.code_paths && <CodePaths paths={proposal.payload.code_paths} title="Proposed code paths" />}
        </div>
    );
}

function DeleteDetail({ proposal }: { proposal: CaseProposal }) {
    const { data: targetCase, isLoading } = useQuery<TestCaseInfo>({
        queryKey: ['case', proposal.target_case_id],
        queryFn: () => getTestCaseInfo(proposal.target_case_id!),
        enabled: !!proposal.target_case_id,
        retry: false,
    });

    return (
        <div className="flex flex-col gap-4">
            <div className="flex items-start gap-3 rounded-2xl border border-border p-4">
                <KindChip kind="failure" size="sm" />
                <div className="min-w-0">
                    <p className="text-sm font-semibold text-ink">
                        {isLoading ? 'Loading case…' : targetCase ? `"${targetCase.name}"` : `Case #${proposal.target_case_id}`} will be permanently deleted
                    </p>
                    {targetCase && (
                        <p className="mt-1 text-xs text-ink-soft">{targetCase.steps?.length || 0} steps · suite #{targetCase.test_suite_id ?? '—'}</p>
                    )}
                    {(proposal.payload?.reason || proposal.rationale) && (
                        <p className="mt-2 text-sm text-ink">{proposal.payload?.reason || proposal.rationale}</p>
                    )}
                </div>
            </div>
            {targetCase && targetCase.steps?.length > 0 && (
                <StepList steps={targetCase.steps} title="Steps being removed" />
            )}
        </div>
    );
}

function ProposalBody({ proposal }: { proposal: CaseProposal }) {
    switch (proposal.action) {
        case 'create':
            return (
                <div className="flex flex-col gap-4">
                    <StepList steps={proposal.payload?.steps || []} title={`Proposed steps for "${proposal.payload?.name || 'Unnamed case'}"`} />
                    {proposal.payload?.code_paths && proposal.payload.code_paths.length > 0 && (
                        <CodePaths paths={proposal.payload.code_paths} title="Code paths" />
                    )}
                </div>
            );
        case 'update':
            return <UpdateDiff proposal={proposal} />;
        case 'delete':
            return <DeleteDetail proposal={proposal} />;
        case 'move':
            return (
                <p className="text-sm text-ink">
                    Move case #{proposal.target_case_id} to suite #{proposal.payload?.new_test_suite_id ?? '—'}.
                </p>
            );
        default:
            return <pre className="overflow-x-auto rounded-2xl bg-background p-4 font-mono text-xs text-ink">{JSON.stringify(proposal.payload, null, 2)}</pre>;
    }
}

const proposalTitle = (proposal: CaseProposal) =>
    proposal.action === 'create'
        ? proposal.payload?.name || 'New test case'
        : proposal.payload?.name || `Case #${proposal.target_case_id}`;

// ---------------------------------------------------------------------------
// Test-proposal detail card
// ---------------------------------------------------------------------------

function ProposalDetail({ proposal }: { proposal: CaseProposal }) {
    const queryClient = useQueryClient();
    const [note, setNote] = useState('');

    const decideMutation = useMutation({
        mutationFn: ({ decision }: { decision: 'accept' | 'reject' }) =>
            decision === 'accept'
                ? caseProposalsApi.accept(proposal.id, note || undefined)
                : caseProposalsApi.reject(proposal.id, note || undefined),
        onSuccess: (_data, vars) => {
            queryClient.invalidateQueries({ queryKey: ['case-proposals'] });
            queryClient.invalidateQueries({ queryKey: ['case', proposal.target_case_id] });
            toast.success(vars.decision === 'accept' ? 'Proposal accepted and applied' : 'Proposal rejected');
        },
        onError: (err: Error & { response?: { data?: { detail?: unknown } } }) =>
            toast.error(`Failed to update proposal: ${detailText(err.response?.data?.detail, err.message)}`),
    });
    const validateMutation = useMutation({
        mutationFn: () => caseProposalsApi.validate(proposal.id),
        onSuccess: (out) => {
            queryClient.invalidateQueries({ queryKey: ['case-proposals'] });
            toast.success(`Validation run #${out.run_id} started`);
        },
        onError: (err: Error & { response?: { data?: { detail?: unknown } } }) =>
            toast.error(`Couldn't start a validation run: ${detailText(err.response?.data?.detail, err.message)}`),
    });
    const block = acceptBlock(proposal);

    const actionStyle = ACTION_STYLES[proposal.action] || ACTION_STYLES.update;
    const st = statusStyle(proposal.status);

    const facts: { tag: string; text: string; to?: string }[] = [
        { tag: 'project', text: `Project #${proposal.project_id}` },
    ];
    if (proposal.test_suite_id != null) facts.push({ tag: 'suite', text: `Suite #${proposal.test_suite_id}`, to: `/suites/${proposal.test_suite_id}` });
    if (proposal.source_run_id != null) facts.push({ tag: 'run', text: `Source run #${proposal.source_run_id}`, to: `/runs/${proposal.source_run_id}` });
    if (proposal.decided_at) facts.push({ tag: 'decided', text: formatDate(proposal.decided_at) });

    return (
        <DetailCard label="Selected test proposal">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-2">
                    <Pill tone={actionStyle.tone}>{actionStyle.label}</Pill>
                    <Pill tone={st.tone}>{st.label}</Pill>
                    <ConfidenceBadge value={proposal.ai_confidence} />
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{proposalTitle(proposal)}</h2>
                <span className="flex flex-wrap items-center gap-1.5 text-sm text-ink-soft">
                    <Bot className="h-4 w-4" aria-hidden="true" />
                    {proposal.agent_id || 'An agent'} wants to {actionStyle.verb} · {formatDate(proposal.created_at)}
                </span>
            </div>

            {proposal.rationale && <Panel title="Why the agent proposes this" callout>{proposal.rationale}</Panel>}

            <ProposalBody proposal={proposal} />

            <EvidenceList items={facts} />

            <ValidationPanel
                proposal={proposal}
                canValidate={caseValidatable(proposal)}
                onValidate={() => validateMutation.mutate()}
                busy={validateMutation.isPending}
                block={block}
            />

            {proposal.status === 'pending' && (
                <div className="flex flex-col gap-3 border-t border-border pt-5">
                    <label className="flex flex-col gap-1.5 text-[13px] text-ink-soft">
                        Review note (optional)
                        <Input
                            placeholder="Why you accepted or rejected it"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            className="h-11 rounded-full bg-white text-ink"
                        />
                    </label>
                    <div className="flex flex-wrap gap-2.5">
                        <Button
                            size="lg"
                            disabled={decideMutation.isPending || !!block}
                            title={block ?? undefined}
                            onClick={() => decideMutation.mutate({ decision: 'accept' })}
                        >
                            <Check className="h-4 w-4" /> Accept and apply
                        </Button>
                        <Button
                            size="lg"
                            variant="outline"
                            disabled={decideMutation.isPending}
                            onClick={() => decideMutation.mutate({ decision: 'reject' })}
                        >
                            <X className="h-4 w-4" /> Reject
                        </Button>
                    </div>
                </div>
            )}
        </DetailCard>
    );
}

function CaseProposalsView({ proposals }: { proposals: CaseProposal[] }) {
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const groups = ACTION_ORDER
        .map((a) => ({ action: a, items: proposals.filter((p) => p.action === a) }))
        .concat([{ action: 'other', items: proposals.filter((p) => !ACTION_ORDER.includes(p.action)) }])
        .filter((g) => g.items.length > 0);
    const selected = proposals.find((p) => p.id === selectedId) ?? groups[0]?.items[0] ?? null;

    return (
        <SplitView
            list={groups.map((g) => (
                <ListGroup key={g.action} title={ACTION_STYLES[g.action]?.label ?? 'Other'} count={g.items.length}>
                    {g.items.map((p) => (
                        <ListRow
                            key={p.id}
                            selected={selected?.id === p.id}
                            onSelect={() => setSelectedId(p.id)}
                            leading={<KindChip kind={p.status === 'accepted' ? 'pass' : p.status === 'rejected' ? 'skip' : 'review'} size="sm" />}
                            title={<span className="line-clamp-2 break-words">{proposalTitle(p)}</span>}
                            meta={`${p.agent_id || 'agent'} · ${ago(p.created_at)}`}
                            trailing={<span className="font-mono text-xs tabular-nums text-ink-soft" title="Agent confidence">{Math.round(p.ai_confidence * 100)}%</span>}
                        />
                    ))}
                </ListGroup>
            ))}
            detail={selected && <ProposalDetail key={selected.id} proposal={selected} />}
        />
    );
}

// ---------------------------------------------------------------------------
// Heal proposals
// ---------------------------------------------------------------------------

function HealDetail({ proposal: p, caseName, onDecide, onValidate, busy, refusal }: {
    proposal: HealProposal;
    caseName: string;
    onDecide: (decision: 'accept' | 'reject', selector?: string) => void;
    onValidate: (selector: string) => void;
    busy: boolean;
    /** Why the last accept of this proposal was refused (409), if it was. */
    refusal?: string | null;
}) {
    const st = statusStyle(p.status);
    const candidates = candidatesOf(p);
    const legacy = !p.candidates || p.candidates.length === 0;
    const [picked, setPicked] = useState(defaultPick(p));
    const [recheck, setRecheck] = useState<HealRecheck | null>(p.recheck ?? null);
    const recheckMutation = useMutation({
        mutationFn: (selector: string) => healProposalsApi.recheck(p.id, selector),
        onSuccess: (r) => setRecheck(r),
        onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
            toast.error(`Couldn't check the selector: ${err.response?.data?.detail || err.message}`),
    });
    const pick = (selector: string) => {
        setPicked(selector);
        setRecheck(null);
    };
    const block = acceptBlock(p, picked);
    return (
        <DetailCard label="Selected heal proposal">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap gap-2">
                    <Pill tone={st.tone}>{st.label}</Pill>
                    <ConfidenceBadge value={p.confidence} />
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{caseName}</h2>
                <span className="text-sm text-ink-soft">
                    Step <span className="font-mono">{p.step_id}</span> · proposed {formatDate(p.created_at)}
                </span>
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow>Selector change</Eyebrow>
                <div className="overflow-hidden rounded-2xl border border-border">
                    <div className="flex items-start gap-3 px-4 py-3">
                        <span className="w-14 flex-none pt-0.5 text-xs font-semibold text-ink-soft">Before</span>
                        <code className="min-w-0 flex-1 break-all rounded-lg bg-pastel-peach/70 px-2 py-1 font-mono text-xs text-ink line-through decoration-ink-muted">
                            {p.old_selector || '(none)'}
                        </code>
                    </div>
                    <div className="flex items-start gap-3 border-t border-[#F0EEE8] px-4 py-3">
                        <span className="w-14 flex-none pt-0.5 text-xs font-semibold text-ink-soft">After</span>
                        <code className="min-w-0 flex-1 break-all rounded-lg bg-pastel-mint/70 px-2 py-1 font-mono text-xs text-ink">
                            {p.status === 'pending' ? picked : (p.chosen_selector || p.new_selector)}
                        </code>
                    </div>
                </div>
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow>{legacy ? 'Proposed selector' : `Ranked candidates (${candidates.length})`}</Eyebrow>
                {!legacy && (
                    <p className="text-xs text-ink-soft">
                        Only a candidate matching exactly one element can be applied. Accepting rechecks
                        the pick against the redacted page captured when the step failed
                        {p.has_dom_snapshot ? '' : ' (no snapshot was stored for this proposal)'}.
                    </p>
                )}
                <HealCandidateList
                    candidates={candidates}
                    picked={picked}
                    onPick={pick}
                    legacy={legacy}
                    editable={p.status === 'pending' && !busy}
                    onRecheck={p.has_dom_snapshot ? () => recheckMutation.mutate(picked) : undefined}
                    recheck={recheck}
                    checking={recheckMutation.isPending}
                />
                {refusal && (
                    <p role="alert" className="rounded-2xl border border-border bg-pastel-pink/40 px-4 py-3 text-xs text-ink">
                        Not applied: {refusal}
                    </p>
                )}
            </div>

            {(p.intent || p.rationale) && (
                <Panel title="Why TraceIQ suggests this" callout>
                    {p.intent && <p className="font-semibold">{p.intent}</p>}
                    {p.rationale && <p className={cn(p.intent && 'mt-1')}>{p.rationale}</p>}
                </Panel>
            )}

            <EvidenceList
                items={[
                    { tag: 'case', text: `Case #${p.test_case_id}` },
                    ...(p.source_run_id != null ? [{ tag: 'run', text: `Found after run #${p.source_run_id}`, to: `/runs/${p.source_run_id}` }] : []),
                ]}
            />

            <ValidationPanel
                proposal={p}
                canValidate
                onValidate={() => onValidate(picked)}
                busy={busy}
                block={block}
            />

            {p.status === 'pending' && (
                <div className="flex flex-wrap gap-2.5 border-t border-border pt-5">
                    <Button size="lg" disabled={busy || !!block} title={block ?? undefined}
                        onClick={() => onDecide('accept', picked)}>
                        <Check className="h-4 w-4" /> Apply to the test
                    </Button>
                    <Button size="lg" variant="outline" disabled={busy} onClick={() => onDecide('reject')}>
                        <X className="h-4 w-4" /> Reject
                    </Button>
                </div>
            )}
        </DetailCard>
    );
}

function HealProposalsTab({ status }: { status: string }) {
    const queryClient = useQueryClient();
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const { data: proposals, isLoading, error } = useQuery({
        queryKey: ['heal-proposals', status],
        queryFn: () => healProposalsApi.list(status || undefined),
        // Poll while a validation run is in flight so its outcome shows up.
        refetchInterval: (q) => (anyValidating(q.state.data) ? 5000 : false),
    });

    const caseIds = useMemo(
        () => Array.from(new Set((proposals || []).map((p) => p.test_case_id))),
        [proposals],
    );
    const caseQueries = useQueries({
        queries: caseIds.map((id) => ({
            queryKey: ['case', id],
            queryFn: () => getTestCaseInfo(id),
            staleTime: 60_000,
            retry: false,
        })),
    });
    const caseNames = useMemo(() => {
        const map = new Map<number, string>();
        caseQueries.forEach((q, i) => {
            if (q.data) map.set(caseIds[i], q.data.name);
        });
        return map;
    }, [caseQueries, caseIds]);

    // Accept refusals (409: assertion step, stale, or the recheck found the
    // pick not unique) are shown next to the candidates, per proposal.
    const [refusals, setRefusals] = useState<Record<number, string>>({});
    const decideMutation = useMutation({
        mutationFn: ({ id, decision, selector }: { id: number; decision: 'accept' | 'reject'; selector?: string }) =>
            decision === 'accept' ? healProposalsApi.accept(id, selector) : healProposalsApi.reject(id),
        onSuccess: (_data, vars) => {
            queryClient.invalidateQueries({ queryKey: ['heal-proposals'] });
            setRefusals((r) => {
                const next = { ...r };
                delete next[vars.id];
                return next;
            });
            toast.success(vars.decision === 'accept' ? 'Selector heal applied to the test case' : 'Heal proposal rejected');
        },
        onError: (err: Error & { response?: { status?: number; data?: { detail?: unknown } } }, vars) => {
            const detail = detailText(err.response?.data?.detail, err.message);
            if (err.response?.status === 409) setRefusals((r) => ({ ...r, [vars.id]: detail }));
            toast.error(`Failed to update heal proposal: ${detail}`);
        },
    });

    const validateMutation = useMutation({
        mutationFn: ({ id, selector }: { id: number; selector: string }) => healProposalsApi.validate(id, selector),
        onSuccess: (out) => {
            queryClient.invalidateQueries({ queryKey: ['heal-proposals'] });
            toast.success(`Validation run #${out.run_id} started`);
        },
        onError: (err: Error & { response?: { data?: { detail?: unknown } } }) =>
            toast.error(`Couldn't start a validation run: ${detailText(err.response?.data?.detail, err.message)}`),
    });

    if (isLoading) {
        return (
            <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
            </div>
        );
    }
    if (error) {
        return <EmptyState title="Couldn't load heal proposals">{(error as Error).message}</EmptyState>;
    }
    if (!proposals || proposals.length === 0) {
        return (
            <EmptyState title={`No ${status ? `${statusStyle(status).label.toLowerCase()} ` : ''}heal proposals`}>
                After successful runs, TraceIQ compares stored selectors with the captured page and suggests fixes for the ones that drifted.
            </EmptyState>
        );
    }

    const nameOf = (p: HealProposal) => caseNames.get(p.test_case_id) || `Case #${p.test_case_id}`;
    const groups = [
        { name: 'High confidence', items: proposals.filter((p) => p.confidence >= 0.8) },
        { name: 'Worth a closer look', items: proposals.filter((p) => p.confidence < 0.8) },
    ].filter((g) => g.items.length > 0);
    const selected = proposals.find((p) => p.id === selectedId) ?? groups[0]?.items[0] ?? null;

    return (
        <SplitView
            list={groups.map((g) => (
                <ListGroup key={g.name} title={g.name} count={g.items.length}>
                    {g.items.map((p) => (
                        <ListRow
                            key={p.id}
                            selected={selected?.id === p.id}
                            onSelect={() => setSelectedId(p.id)}
                            leading={<KindChip kind={p.status === 'pending' ? 'review' : p.status === 'rejected' ? 'skip' : 'pass'} size="sm" />}
                            title={<span className="line-clamp-2 break-words">{nameOf(p)}</span>}
                            meta={`Step ${p.step_id}${p.source_run_id != null ? ` · run #${p.source_run_id}` : ''} · ${ago(p.created_at)}`}
                            trailing={<span className="font-mono text-xs tabular-nums text-ink-soft" title="Confidence">{Math.round(p.confidence * 100)}%</span>}
                        />
                    ))}
                </ListGroup>
            ))}
            detail={selected && (
                <HealDetail
                    key={selected.id}
                    proposal={selected}
                    caseName={nameOf(selected)}
                    busy={decideMutation.isPending || validateMutation.isPending}
                    onValidate={(selector) => validateMutation.mutate({ id: selected.id, selector })}
                    refusal={refusals[selected.id] ?? null}
                    onDecide={(decision, selector) => decideMutation.mutate({ id: selected.id, decision, selector })}
                />
            )}
        />
    );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const CASE_STATUSES = ['pending', 'accepted', 'rejected'] as const;
const HEAL_STATUSES = ['pending', 'accepted', 'rejected', 'auto_applied'] as const;
const STATUS_FILTER_LABEL: Record<string, string> = {
    pending: 'Waiting', accepted: 'Accepted', rejected: 'Rejected', auto_applied: 'Auto-applied',
};

export default function Proposals() {
    const projectId = useActiveProjectId();
    const [tab, setTab] = useState<'cases' | 'heals'>('cases');
    const [status, setStatus] = useState<string>('pending');

    const { data: caseProposals, isLoading, error } = useQuery({
        queryKey: ['case-proposals', projectId, status],
        queryFn: () => caseProposalsApi.list(projectId || undefined, status || undefined),
        enabled: tab === 'cases' && !!projectId,
        refetchInterval: (q) => (anyValidating(q.state.data) ? 5000 : false),
    });

    const statuses = tab === 'cases' ? CASE_STATUSES : HEAL_STATUSES;

    const tabs = [
        { id: 'cases' as const, label: 'Test changes' },
        { id: 'heals' as const, label: 'Selector fixes' },
    ];

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Proposals"
                subtitle="Changes AI agents want to make to your tests. Nothing touches a suite until someone here accepts it."
                toolbar={<>
                    <PanelTabs
                        label="Proposal type"
                        value={tab}
                        onChange={(id) => { setTab(id); setStatus('pending'); }}
                        tabs={tabs}
                    />
                    <div className="ml-auto flex flex-wrap gap-1 rounded-full bg-[#EFEDE7] p-1" aria-label="Filter by status" role="group">
                        {statuses.map((s) => (
                            <button
                                key={s}
                                type="button"
                                aria-pressed={status === s}
                                onClick={() => setStatus(s)}
                                className={cn(
                                    'min-h-9 rounded-full px-3.5 text-[13px] text-ink outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ink/20',
                                    status === s ? 'bg-white font-semibold shadow-[0_1px_4px_rgba(28,27,34,0.1)]' : 'font-medium text-ink-soft hover:text-ink'
                                )}
                            >
                                {STATUS_FILTER_LABEL[s] ?? s.replace('_', ' ')}
                            </button>
                        ))}
                    </div>
                </>}
            />

            {projectId && <AutoApplyPolicyCard projectId={projectId} />}

            {tab === 'heals' ? (
                <HealProposalsTab status={status} />
            ) : !projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its proposal queue.</EmptyState>
            ) : isLoading ? (
                <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : error ? (
                <EmptyState title="Couldn't load proposals">{(error as Error).message}</EmptyState>
            ) : !caseProposals || caseProposals.length === 0 ? (
                <EmptyState title={`No ${statusStyle(status).label.toLowerCase()} test proposals`}>
                    When AI agents propose creating, updating or deleting tests, they land here for your review.
                </EmptyState>
            ) : (
                <CaseProposalsView proposals={caseProposals} />
            )}
        </div>
    );
}
