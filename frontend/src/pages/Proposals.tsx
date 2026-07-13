import { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Bot, Check, X, ChevronDown, ChevronRight, RefreshCw, AlertCircle, Inbox,
  PlusCircle, PenLine, Trash2, MoveRight, Wrench, GitPullRequestArrow, FileText,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { toast } from 'sonner';
import {
  caseProposalsApi, healProposalsApi, getTestCaseInfo,
  CaseProposal, HealProposal, ProposedStep, TestCaseInfo,
} from '@/api/proposals';

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

const formatDate = (dateString?: string | null) => {
  if (!dateString) return '—';
  return new Date(dateString).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

const stepSignature = (s: ProposedStep) =>
  [s.type, s.selector || '', s.value || '', s.intent || ''].join('');

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

const ACTION_STYLES: Record<string, { label: string; className: string; Icon: typeof PlusCircle }> = {
  create: { label: 'Create', className: 'bg-emerald-50 text-emerald-700 border-emerald-200', Icon: PlusCircle },
  update: { label: 'Update', className: 'bg-sky-50 text-sky-700 border-sky-200', Icon: PenLine },
  delete: { label: 'Delete', className: 'bg-rose-50 text-rose-700 border-rose-200', Icon: Trash2 },
  move: { label: 'Move', className: 'bg-amber-50 text-amber-700 border-amber-200', Icon: MoveRight },
};

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-amber-50 text-amber-700 border-amber-200',
  accepted: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  rejected: 'bg-rose-50 text-rose-700 border-rose-200',
  auto_applied: 'bg-indigo-50 text-indigo-700 border-indigo-200',
};

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.8
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : value >= 0.5
      ? 'bg-amber-50 text-amber-700 border-amber-200'
      : 'bg-slate-50 text-slate-500 border-slate-200';
  return (
    <Badge variant="outline" className={`text-[10px] font-bold rounded-md ${tone}`}>
      {pct}% confidence
    </Badge>
  );
}

function StepList({ steps, title }: { steps: ProposedStep[]; title?: string }) {
  if (!steps.length) {
    return <p className="text-sm text-slate-400 italic">No steps</p>;
  }
  return (
    <div>
      {title && <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2">{title}</p>}
      <ol className="space-y-1.5">
        {steps.map((s, i) => (
          <li key={s.id || i} className="flex items-start gap-2 text-sm">
            <span className="shrink-0 w-6 h-6 rounded-md bg-slate-100 text-slate-500 text-xs font-bold flex items-center justify-center mt-0.5">
              {i + 1}
            </span>
            <div className="min-w-0">
              <span className="font-mono text-xs font-bold text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded mr-2">{s.type}</span>
              {s.selector && <code className="text-xs text-slate-600 break-all">{s.selector}</code>}
              {s.value && <span className="text-xs text-slate-500 ml-1">= "{s.value}"</span>}
              {s.intent && <p className="text-xs text-slate-400 italic mt-0.5">{s.intent}</p>}
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
  if (!step) return <div className="px-3 py-2 text-xs text-slate-300 italic bg-slate-50/50">—</div>;
  const highlight =
    kind === 'same' ? 'bg-white' :
      kind === 'changed' ? (side === 'current' ? 'bg-rose-50/60' : 'bg-emerald-50/60') :
        kind === 'added' ? 'bg-emerald-50/60' : 'bg-rose-50/60';
  return (
    <div className={`px-3 py-2 text-xs font-mono break-all ${highlight}`}>
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

  if (isLoading) return <div className="flex items-center gap-2 text-sm text-slate-400 py-4"><RefreshCw className="w-4 h-4 animate-spin" /> Loading current case…</div>;
  if (error || !currentCase) return <p className="text-sm text-rose-600 py-2">Could not load current case #{proposal.target_case_id} for diffing.</p>;

  const proposedSteps = proposal.payload?.steps;
  const proposedName = proposal.payload?.name;
  const rows = proposedSteps ? diffSteps(currentCase.steps || [], proposedSteps) : [];

  return (
    <div className="space-y-4">
      {proposedName && proposedName !== currentCase.name && (
        <div className="flex items-center gap-2 text-sm">
          <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Rename</span>
          <span className="line-through text-rose-600">{currentCase.name}</span>
          <MoveRight className="w-3.5 h-3.5 text-slate-400" />
          <span className="font-semibold text-emerald-700">{proposedName}</span>
        </div>
      )}
      {proposedSteps ? (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
          <div className="grid grid-cols-2 bg-slate-50 border-b border-slate-200">
            <div className="px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest">Current steps ({currentCase.steps?.length || 0})</div>
            <div className="px-3 py-2 text-[10px] font-bold text-slate-400 uppercase tracking-widest border-l border-slate-200">Proposed steps ({proposedSteps.length})</div>
          </div>
          {rows.map((row, i) => (
            <div key={i} className="grid grid-cols-2 border-b border-slate-100 last:border-b-0">
              <DiffCell step={row.current} kind={row.kind} side="current" />
              <div className="border-l border-slate-100">
                <DiffCell step={row.proposed} kind={row.kind} side="proposed" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-slate-500 italic">No step changes proposed (metadata-only update).</p>
      )}
      {proposal.payload?.code_paths && (
        <div>
          <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Proposed code paths</p>
          <div className="flex flex-wrap gap-1.5">
            {proposal.payload.code_paths.map((p) => (
              <code key={p} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">{p}</code>
            ))}
          </div>
        </div>
      )}
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
    <div className="space-y-3">
      <div className="flex items-start gap-3 p-4 bg-rose-50/60 border border-rose-100 rounded-xl">
        <Trash2 className="w-5 h-5 text-rose-500 shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-bold text-rose-800">
            {isLoading ? 'Loading case…' : targetCase ? `"${targetCase.name}"` : `Case #${proposal.target_case_id}`} will be permanently deleted
          </p>
          {targetCase && (
            <p className="text-xs text-rose-600/80 mt-1">{targetCase.steps?.length || 0} steps · suite #{targetCase.test_suite_id ?? '—'}</p>
          )}
          {(proposal.payload?.reason || proposal.rationale) && (
            <p className="text-sm text-slate-600 mt-2">{proposal.payload?.reason || proposal.rationale}</p>
          )}
        </div>
      </div>
      {targetCase && targetCase.steps?.length > 0 && (
        <StepList steps={targetCase.steps} title="Steps being removed" />
      )}
    </div>
  );
}

function ProposalDetail({ proposal }: { proposal: CaseProposal }) {
  switch (proposal.action) {
    case 'create':
      return (
        <div className="space-y-4">
          <StepList steps={proposal.payload?.steps || []} title={`Proposed steps for "${proposal.payload?.name || 'Unnamed case'}"`} />
          {proposal.payload?.code_paths && proposal.payload.code_paths.length > 0 && (
            <div>
              <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1">Code paths</p>
              <div className="flex flex-wrap gap-1.5">
                {proposal.payload.code_paths.map((p) => (
                  <code key={p} className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">{p}</code>
                ))}
              </div>
            </div>
          )}
        </div>
      );
    case 'update':
      return <UpdateDiff proposal={proposal} />;
    case 'delete':
      return <DeleteDetail proposal={proposal} />;
    case 'move':
      return (
        <p className="text-sm text-slate-600">
          Move case #{proposal.target_case_id} to suite #{proposal.payload?.new_test_suite_id ?? '—'}.
        </p>
      );
    default:
      return <pre className="text-xs bg-slate-50 p-3 rounded-xl overflow-x-auto">{JSON.stringify(proposal.payload, null, 2)}</pre>;
  }
}

// ---------------------------------------------------------------------------
// Test-proposal card
// ---------------------------------------------------------------------------

function ProposalCard({ proposal }: { proposal: CaseProposal }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
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
    onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
      toast.error(`Failed to update proposal: ${err.response?.data?.detail || err.message}`),
  });

  const actionStyle = ACTION_STYLES[proposal.action] || ACTION_STYLES.update;
  const title =
    proposal.action === 'create'
      ? proposal.payload?.name || 'New test case'
      : proposal.payload?.name || `Case #${proposal.target_case_id}`;

  return (
    <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-slate-50/60 transition-colors"
        onClick={() => setExpanded((e) => !e)}
      >
        {expanded ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
        <Badge variant="outline" className={`rounded-lg font-bold text-[10px] uppercase tracking-wide shrink-0 ${actionStyle.className}`}>
          <actionStyle.Icon className="w-3 h-3 mr-1" /> {actionStyle.label}
        </Badge>
        <span className="font-bold text-slate-800 truncate">{title}</span>
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <Badge variant="outline" className="rounded-lg bg-violet-50 text-violet-700 border-violet-200 text-[10px] font-semibold max-w-[160px] truncate">
            <Bot className="w-3 h-3 mr-1 shrink-0" /> {proposal.agent_id || 'agent'}
          </Badge>
          <ConfidenceBadge value={proposal.ai_confidence} />
          <Badge variant="outline" className={`rounded-lg text-[10px] font-bold uppercase ${STATUS_STYLES[proposal.status] || ''}`}>
            {proposal.status}
          </Badge>
          <span className="text-xs text-slate-400 whitespace-nowrap hidden sm:inline">{formatDate(proposal.created_at)}</span>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-100 px-5 py-4 space-y-4 bg-slate-50/30">
          {proposal.rationale && (
            <div className="flex items-start gap-2 text-sm text-slate-600 bg-white border border-slate-100 rounded-xl p-3">
              <FileText className="w-4 h-4 text-slate-400 shrink-0 mt-0.5" />
              <p>{proposal.rationale}</p>
            </div>
          )}
          <ProposalDetail proposal={proposal} />
          <div className="text-xs text-slate-400 flex flex-wrap gap-x-4 gap-y-1">
            <span>Project #{proposal.project_id}</span>
            {proposal.test_suite_id != null && <span>Suite #{proposal.test_suite_id}</span>}
            {proposal.source_run_id != null && <span>Source run #{proposal.source_run_id}</span>}
            {proposal.decided_at && <span>Decided {formatDate(proposal.decided_at)}</span>}
          </div>
          {proposal.status === 'pending' && (
            <div className="flex flex-col sm:flex-row gap-2 pt-1">
              <Input
                placeholder="Optional review note…"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                className="bg-white border-slate-200 rounded-xl flex-1"
              />
              <div className="flex gap-2 shrink-0">
                <Button
                  size="sm"
                  disabled={decideMutation.isPending}
                  onClick={() => decideMutation.mutate({ decision: 'accept' })}
                  className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl h-10 px-4"
                >
                  <Check className="w-4 h-4 mr-1.5" /> Accept
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={decideMutation.isPending}
                  onClick={() => decideMutation.mutate({ decision: 'reject' })}
                  className="text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200 rounded-xl h-10 px-4"
                >
                  <X className="w-4 h-4 mr-1.5" /> Reject
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Heal-proposal tab
// ---------------------------------------------------------------------------

function HealProposalsTab({ status }: { status: string }) {
  const queryClient = useQueryClient();
  const { data: proposals, isLoading, error } = useQuery({
    queryKey: ['heal-proposals', status],
    queryFn: () => healProposalsApi.list(status || undefined),
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

  const decideMutation = useMutation({
    mutationFn: ({ id, decision }: { id: number; decision: 'accept' | 'reject' }) =>
      decision === 'accept' ? healProposalsApi.accept(id) : healProposalsApi.reject(id),
    onSuccess: (_data, vars) => {
      queryClient.invalidateQueries({ queryKey: ['heal-proposals'] });
      toast.success(vars.decision === 'accept' ? 'Selector heal applied to the test case' : 'Heal proposal rejected');
    },
    onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
      toast.error(`Failed to update heal proposal: ${err.response?.data?.detail || err.message}`),
  });

  if (isLoading) return <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>;
  if (error) {
    return (
      <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
        <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
        <p className="text-rose-700 font-semibold">Failed to load heal proposals</p>
        <p className="text-rose-600/80 text-sm">{(error as Error).message}</p>
      </div>
    );
  }
  if (!proposals || proposals.length === 0) {
    return (
      <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
        <Wrench className="w-10 h-10 text-slate-300 mx-auto mb-4" />
        <h3 className="text-lg font-bold text-slate-800 mb-1">No {status || ''} heal proposals</h3>
        <p className="text-slate-500 text-sm max-w-md mx-auto">
          After successful runs, TraceIQ diffs stored selectors against the captured DOM and proposes heals for drifted selectors here.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
      <div className="overflow-x-auto">
        <Table>
          <TableHeader className="bg-slate-50/50">
            <TableRow className="border-slate-100 hover:bg-transparent">
              <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Test Case</TableHead>
              <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Selector Change</TableHead>
              <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Context</TableHead>
              <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[130px]">Confidence</TableHead>
              <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[110px]">Created</TableHead>
              <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {proposals.map((p: HealProposal) => (
              <TableRow key={p.id} className="border-slate-100 hover:bg-slate-50/50 transition-colors align-top">
                <TableCell className="py-4">
                  <div className="font-bold text-slate-800 text-sm">{caseNames.get(p.test_case_id) || `Case #${p.test_case_id}`}</div>
                  <div className="text-xs text-slate-400 mt-0.5 font-mono">step {p.step_id}</div>
                  {p.source_run_id != null && <div className="text-[10px] text-slate-400 mt-0.5">from run #{p.source_run_id}</div>}
                </TableCell>
                <TableCell className="py-4">
                  <div className="space-y-1 max-w-[340px]">
                    <code className="block text-xs bg-rose-50 text-rose-700 border border-rose-100 px-2 py-1 rounded-md break-all line-through decoration-rose-300">
                      {p.old_selector || '(none)'}
                    </code>
                    <code className="block text-xs bg-emerald-50 text-emerald-700 border border-emerald-100 px-2 py-1 rounded-md break-all">
                      {p.new_selector}
                    </code>
                  </div>
                </TableCell>
                <TableCell className="py-4">
                  <div className="max-w-[280px]">
                    {p.intent && <p className="text-xs font-semibold text-slate-600">{p.intent}</p>}
                    {p.rationale && <p className="text-xs text-slate-400 mt-1 line-clamp-3" title={p.rationale}>{p.rationale}</p>}
                    {!p.intent && !p.rationale && <span className="text-xs text-slate-300 italic">—</span>}
                  </div>
                </TableCell>
                <TableCell className="py-4">
                  <ConfidenceBadge value={p.confidence} />
                  <div className="mt-1.5">
                    <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${STATUS_STYLES[p.status] || ''}`}>
                      {p.status}
                    </Badge>
                  </div>
                </TableCell>
                <TableCell className="py-4 text-xs text-slate-500 whitespace-nowrap">{formatDate(p.created_at)}</TableCell>
                <TableCell className="py-4 text-right">
                  {p.status === 'pending' ? (
                    <div className="flex justify-end gap-1.5">
                      <Button
                        size="sm"
                        disabled={decideMutation.isPending}
                        onClick={() => decideMutation.mutate({ id: p.id, decision: 'accept' })}
                        className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg h-8 px-3"
                      >
                        <Check className="w-3.5 h-3.5 mr-1" /> Apply
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={decideMutation.isPending}
                        onClick={() => decideMutation.mutate({ id: p.id, decision: 'reject' })}
                        className="text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200 rounded-lg h-8 px-3"
                      >
                        <X className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  ) : (
                    <span className="text-xs text-slate-400">—</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const CASE_STATUSES = ['pending', 'accepted', 'rejected'] as const;
const HEAL_STATUSES = ['pending', 'accepted', 'rejected', 'auto_applied'] as const;

export default function Proposals() {
  const [projectId, setProjectId] = useState<number | null>(() => {
    const saved = localStorage.getItem('activeProjectId');
    return saved ? parseInt(saved) : null;
  });
  const [tab, setTab] = useState<'cases' | 'heals'>('cases');
  const [status, setStatus] = useState<string>('pending');

  useEffect(() => {
    const handleProjectChange = () => {
      const saved = localStorage.getItem('activeProjectId');
      setProjectId(saved ? parseInt(saved) : null);
    };
    window.addEventListener('projectChanged', handleProjectChange);
    return () => window.removeEventListener('projectChanged', handleProjectChange);
  }, []);

  const { data: caseProposals, isLoading, error } = useQuery({
    queryKey: ['case-proposals', projectId, status],
    queryFn: () => caseProposalsApi.list(projectId || undefined, status || undefined),
    enabled: tab === 'cases' && !!projectId,
  });

  const statuses = tab === 'cases' ? CASE_STATUSES : HEAL_STATUSES;

  return (
    <div className="max-w-[1400px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Agent Review Queue</span>
        </div>
        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Proposal Review</h1>
        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
          Review changes proposed by AI agents before they touch the suite — new, updated or deleted test cases, and selector heals detected after runs.
        </p>
      </div>

      <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-6">
        <div className="flex bg-white rounded-xl border border-slate-200 shadow-sm p-1">
          <Button
            variant="ghost" size="sm"
            className={`px-4 h-9 rounded-lg ${tab === 'cases' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            onClick={() => { setTab('cases'); setStatus('pending'); }}
          >
            <GitPullRequestArrow className="w-4 h-4 mr-2" /> Test Proposals
          </Button>
          <Button
            variant="ghost" size="sm"
            className={`px-4 h-9 rounded-lg ${tab === 'heals' ? 'bg-slate-100 text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'}`}
            onClick={() => { setTab('heals'); setStatus('pending'); }}
          >
            <Wrench className="w-4 h-4 mr-2" /> Heal Proposals
          </Button>
        </div>

        <div className="flex gap-1.5 sm:ml-auto">
          {statuses.map((s) => (
            <Button
              key={s}
              variant="outline"
              size="sm"
              onClick={() => setStatus(s)}
              className={`h-9 rounded-xl text-xs font-bold uppercase tracking-wide ${status === s
                ? 'bg-indigo-50 text-indigo-700 border-indigo-200'
                : 'bg-white text-slate-500 border-slate-200 hover:bg-slate-50'}`}
            >
              {s.replace('_', ' ')}
            </Button>
          ))}
        </div>
      </div>

      {tab === 'heals' ? (
        <HealProposalsTab status={status} />
      ) : !projectId ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
          <p className="text-slate-500 text-sm">Select a project from the top navigation to view its proposal queue.</p>
        </div>
      ) : isLoading ? (
        <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
      ) : error ? (
        <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
          <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
          <p className="text-rose-700 font-semibold">Failed to load proposals</p>
          <p className="text-rose-600/80 text-sm">{(error as Error).message}</p>
        </div>
      ) : !caseProposals || caseProposals.length === 0 ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <Inbox className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No {status} test proposals</h3>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            When AI agents propose creating, updating or deleting test cases, they land here for your review.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {caseProposals.map((p) => <ProposalCard key={p.id} proposal={p} />)}
        </div>
      )}
    </div>
  );
}
