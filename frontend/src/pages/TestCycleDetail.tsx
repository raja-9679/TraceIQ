import { useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ClipboardCheck, Loader2, Play, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { SettingsCard, checkboxClass, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { getTestSuites, triggerRun } from '@/lib/api';
import { errDetail } from '@/lib/errors';
import { cn } from '@/lib/utils';
import { useAuth } from '@/context/AuthContext';
import { collabApi } from '@/api/collaboration';
import { manualTestingApi, type SignoffVerdict } from '@/api/manualTesting';
import { cyclesApi, type CycleDetail, type CycleItem } from '@/api/cycles';
import { ProgressBar } from '@/components/cycles/ProgressBar';
import { selectClass } from '@/components/cycles/styles';
import {
    automatedRunPlan, cycleBadge, dueText, filterItems, isRemaining, itemLink, NO_FILTER, progressText,
    stateLabel, stateTone, STATES, type ItemFilter,
} from '@/components/cycles/model';

type Scope = 'remaining' | 'all';

/** One test cycle: progress, the cases with who tests each, its runs and the sign-off. */
export default function TestCycleDetail() {
    const cycleId = Number(useParams().cycleId);
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const me = useAuth().user?.id ?? null;
    const key = ['test-cycle', cycleId];

    const query = useQuery({
        queryKey: key,
        queryFn: () => cyclesApi.get(cycleId),
        enabled: Number.isFinite(cycleId),
        // Runs finish in the background; keep the picture current while open.
        refetchInterval: (q) => (q.state.data?.runs.some((r) => r.live && r.kind === 'automated') ? 5000 : false),
    });
    const cycle = query.data;

    const [filter, setFilter] = useState<ItemFilter>(NO_FILTER);
    const [picked, setPicked] = useState<number[]>([]);
    const [dialog, setDialog] = useState<null | 'run' | 'manual' | 'add' | 'edit' | 'delete'>(null);

    const set = (d: CycleDetail) => {
        queryClient.setQueryData(key, d);
        queryClient.invalidateQueries({ queryKey: ['test-cycles', d.project_id] });
    };
    const fail = (e: unknown) => toast.error(errDetail(e));

    const candidates = useQuery({
        queryKey: ['assignee-candidates', cycle?.project_id],
        queryFn: () => collabApi.candidates(cycle!.project_id),
        enabled: !!cycle?.can_edit,
        staleTime: 60_000,
    });

    const assign = useMutation({
        mutationFn: (userId: number | null) => cyclesApi.assign(cycleId, picked, userId),
        onSuccess: (d) => { set(d); setPicked([]); },
        onError: fail,
    });
    const removeItems = useMutation({
        mutationFn: () => cyclesApi.removeItems(cycleId, picked),
        onSuccess: (d) => { set(d); setPicked([]); },
        onError: fail,
    });
    const setStatus = useMutation({
        mutationFn: (to: 'close' | 'reopen') => (to === 'close' ? cyclesApi.close(cycleId) : cyclesApi.reopen(cycleId)),
        onSuccess: (d) => { set(d); toast.success(d.status === 'closed' ? 'Cycle closed' : 'Cycle reopened'); },
        onError: fail,
    });
    const remove = useMutation({
        mutationFn: () => cyclesApi.remove(cycleId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['test-cycles', cycle?.project_id] });
            toast.success('Cycle deleted');
            navigate('/test-cycles');
        },
        onError: fail,
    });

    const shown = useMemo(() => (cycle ? filterItems(cycle.items, filter, me) : []), [cycle, filter, me]);

    if (query.isLoading) {
        return <p role="status" className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>;
    }
    if (!cycle) {
        return (
            <div className="flex flex-col gap-5">
                <PageHeader title="Test cycle" crumbs={<Link to="/test-cycles" className="underline underline-offset-2">Test cycles</Link>} />
                <EmptyState title="This cycle is not available">{query.error ? errDetail(query.error) : 'It may have been deleted.'}</EmptyState>
            </div>
        );
    }

    const open = cycle.status === 'open';
    const editable = cycle.can_edit && open;
    const badge = cycleBadge(cycle);
    const due = open ? dueText(cycle.due_date) : null;
    const allShownPicked = shown.length > 0 && shown.every((i) => picked.includes(i.case_id));
    const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
    const people = cycle.assignees.map((a) => a.assignee).filter((p): p is NonNullable<typeof p> => !!p);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title={cycle.name}
                crumbs={<><Link to="/test-cycles" className="underline underline-offset-2">Test cycles</Link><span aria-hidden="true">/</span><span>{cycle.name}</span></>}
                badge={<Pill tone={badge.tone}>{badge.label}</Pill>}
                subtitle={[cycle.release_label && `Release ${cycle.release_label}`, due,
                    cycle.created_by && `started by ${cycle.created_by.name}`,
                    cycle.closed_at && `closed ${new Date(cycle.closed_at).toLocaleDateString()}${cycle.closed_by ? ` by ${cycle.closed_by.name}` : ''}`,
                ].filter(Boolean).join(' · ') || undefined}
                actions={cycle.can_edit ? (
                    <>
                        {open && cycle.manual_cases > 0 && (
                            <Button onClick={() => setDialog('manual')}><ClipboardCheck className="h-4 w-4" aria-hidden="true" /> Test manually</Button>
                        )}
                        {open && cycle.automated_cases > 0 && (
                            <Button variant={cycle.manual_cases > 0 ? 'outline' : 'default'} onClick={() => setDialog('run')}>
                                <Play className="h-4 w-4" aria-hidden="true" /> Run automated tests
                            </Button>
                        )}
                        {open && <Button variant="outline" onClick={() => setDialog('edit')}>Edit</Button>}
                        <Button variant="outline" disabled={setStatus.isPending} onClick={() => setStatus.mutate(open ? 'close' : 'reopen')}>
                            {open ? 'Close cycle' : 'Reopen'}
                        </Button>
                    </>
                ) : undefined}
            />

            <SettingsCard labelledBy="cycle-progress" title="Progress" description={progressText(cycle.progress)}>
                <ProgressBar progress={cycle.progress} legend />
                {cycle.description && <p className="whitespace-pre-wrap text-sm text-ink [overflow-wrap:anywhere]">{cycle.description}</p>}
                {cycle.assignees.length > 0 && cycle.progress.total > 0 && (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[420px] text-sm">
                            <caption className="sr-only">Progress by person</caption>
                            <thead>
                                <tr className="text-left text-xs text-ink-soft">
                                    <th scope="col" className="py-1.5 pr-3 font-semibold">Person</th>
                                    <th scope="col" className="py-1.5 pr-3 font-semibold">Tests</th>
                                    <th scope="col" className="py-1.5 pr-3 font-semibold">Tested</th>
                                    <th scope="col" className="w-[40%] py-1.5 font-semibold">Progress</th>
                                </tr>
                            </thead>
                            <tbody>
                                {cycle.assignees.map((a) => (
                                    <tr key={a.assignee?.id ?? 'none'} className="border-t border-border">
                                        <th scope="row" className="py-2 pr-3 text-left font-medium text-ink">
                                            <button type="button" className="underline underline-offset-2"
                                                onClick={() => setFilter({ ...NO_FILTER, who: a.assignee ? a.assignee.id : 'unassigned' })}>
                                                {a.assignee?.name ?? 'Unassigned'}
                                            </button>
                                        </th>
                                        <td className="py-2 pr-3 text-ink">{a.total}</td>
                                        <td className="py-2 pr-3 text-ink">{a.executed}</td>
                                        <td className="py-2"><ProgressBar progress={a} /></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </SettingsCard>

            <SettingsCard
                labelledBy="cycle-cases"
                title={`Tests (${cycle.items.length})`}
                description="A test's state is the newest result it has in this cycle's runs. Failed and blocked tests stay on the list until a later run passes."
                actions={editable ? <Button variant="outline" onClick={() => setDialog('add')}><Plus className="h-4 w-4" aria-hidden="true" /> Add tests</Button> : undefined}
            >
                {cycle.items.length === 0 ? (
                    <p className="text-sm text-ink-soft">No tests in this cycle yet.{editable ? ' Use "Add tests" to bring in a suite or single tests.' : ''}</p>
                ) : (
                    <>
                        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <label className="flex flex-col gap-1 text-xs font-semibold text-ink">
                                State
                                <select className={selectClass} value={filter.state}
                                    onChange={(e) => setFilter({ ...filter, state: e.target.value as ItemFilter['state'] })}>
                                    <option value="all">All</option>
                                    <option value="remaining">Still to do</option>
                                    {STATES.map((s) => <option key={s} value={s}>{stateLabel[s]}</option>)}
                                </select>
                            </label>
                            <label className="flex flex-col gap-1 text-xs font-semibold text-ink">
                                Assigned to
                                <select className={selectClass} value={String(filter.who)}
                                    onChange={(e) => {
                                        const v = e.target.value;
                                        setFilter({ ...filter, who: v === 'all' || v === 'mine' || v === 'unassigned' ? v : Number(v) });
                                    }}>
                                    <option value="all">Anyone</option>
                                    <option value="mine">Me</option>
                                    <option value="unassigned">Nobody</option>
                                    {people.filter((p) => p.id !== me).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                                </select>
                            </label>
                            <label className="flex flex-col gap-1 text-xs font-semibold text-ink">
                                Kind
                                <select className={selectClass} value={filter.kind}
                                    onChange={(e) => setFilter({ ...filter, kind: e.target.value as ItemFilter['kind'] })}>
                                    <option value="all">Manual and automated</option>
                                    <option value="manual">Manual</option>
                                    <option value="automated">Automated</option>
                                </select>
                            </label>
                            <label className="flex flex-col gap-1 text-xs font-semibold text-ink">
                                Find
                                <Input className={inputClass} value={filter.text} placeholder="Test or suite name"
                                    onChange={(e) => setFilter({ ...filter, text: e.target.value })} />
                            </label>
                        </div>

                        {editable && picked.length > 0 && (
                            <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-background px-4 py-3" role="group" aria-label="Selected tests">
                                <span className="text-sm font-semibold text-ink">{picked.length} selected</span>
                                <label className="sr-only" htmlFor="cycle-assign">Assign to</label>
                                <select id="cycle-assign" className={cn(selectClass, 'w-auto min-w-[200px]')} value="" disabled={assign.isPending}
                                    onChange={(e) => { if (e.target.value) assign.mutate(e.target.value === 'none' ? null : Number(e.target.value)); }}>
                                    <option value="">Assign to…</option>
                                    {(candidates.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                                    <option value="none">Nobody (clear)</option>
                                </select>
                                <Button variant="outline" size="sm" disabled={removeItems.isPending} onClick={() => removeItems.mutate()}>
                                    <Trash2 className="h-4 w-4" aria-hidden="true" /> Remove from cycle
                                </Button>
                                <Button variant="ghost" size="sm" onClick={() => setPicked([])}>Clear</Button>
                            </div>
                        )}

                        {shown.length === 0 ? (
                            <p className="text-sm text-ink-soft">No test matches these filters. <button type="button" className="underline underline-offset-2" onClick={() => setFilter(NO_FILTER)}>Show all</button></p>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full min-w-[640px] text-sm">
                                    <caption className="sr-only">Tests in this cycle</caption>
                                    <thead>
                                        <tr className="text-left text-xs text-ink-soft">
                                            {editable && (
                                                <th scope="col" className="w-8 py-1.5">
                                                    <input type="checkbox" className={checkboxClass} aria-label="Select all shown tests" checked={allShownPicked}
                                                        onChange={() => setPicked(allShownPicked ? picked.filter((id) => !shown.some((i) => i.case_id === id))
                                                            : [...new Set([...picked, ...shown.map((i) => i.case_id)])])} />
                                                </th>
                                            )}
                                            <th scope="col" className="py-1.5 pr-3 font-semibold">Test</th>
                                            <th scope="col" className="py-1.5 pr-3 font-semibold">Assigned to</th>
                                            <th scope="col" className="py-1.5 pr-3 font-semibold">State</th>
                                            <th scope="col" className="py-1.5 font-semibold">Last tested</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {shown.map((i) => <ItemRow key={i.case_id} item={i} editable={editable}
                                            picked={picked.includes(i.case_id)} onToggle={() => toggle(i.case_id)} />)}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </>
                )}
            </SettingsCard>

            <SettingsCard labelledBy="cycle-runs" title={`Runs (${cycle.runs_total})`}
                description="Every run started for this cycle. A run from a suite, a schedule or CI joins by naming the cycle.">
                {cycle.runs.length === 0 ? (
                    <p className="text-sm text-ink-soft">Nothing has been run for this cycle yet.</p>
                ) : (
                    <ul className="flex flex-col divide-y divide-border">
                        {cycle.runs.map((r) => (
                            <li key={r.id} className="flex flex-wrap items-center gap-3 py-2">
                                <div className="min-w-0 flex-1 text-sm">
                                    <span className="font-medium text-ink">#{r.id} {r.suite_name}</span>
                                    <span className="block text-xs text-ink-soft">
                                        {[r.kind === 'manual' ? 'Manual' : [r.browser, r.device].filter(Boolean).join(' / ') || 'Automated',
                                            r.created_at && new Date(r.created_at).toLocaleString(),
                                            r.started_by && `by ${r.started_by.name}`,
                                            !r.live && r.total_tests != null && `${r.passed_tests ?? 0} passed of ${r.total_tests}`,
                                        ].filter(Boolean).join(' · ')}
                                    </span>
                                </div>
                                <Pill tone={r.live ? 'neutral' : r.status === 'passed' ? 'mint' : 'peach'}>{r.live ? 'In progress' : r.status.replace('_', ' ')}</Pill>
                                <Link to={r.kind === 'manual' ? `/manual-runs/${r.id}` : `/runs/${r.id}`} className="text-sm font-medium text-ink underline underline-offset-2">
                                    {r.kind === 'manual' && r.live ? 'Continue testing' : 'Open'}
                                </Link>
                            </li>
                        ))}
                    </ul>
                )}
                {cycle.runs_total > cycle.runs.length && <p className="text-xs text-ink-soft">Showing the newest {cycle.runs.length}.</p>}
            </SettingsCard>

            <SignoffCard cycle={cycle} onSigned={() => queryClient.invalidateQueries({ queryKey: key })} />

            {cycle.can_edit && (
                <SettingsCard labelledBy="cycle-delete" title="Delete this cycle" danger
                    description="Removes the cycle and its list of tests. Runs, their results and sign-offs are kept. Needs project admin.">
                    <div><Button variant="outline" onClick={() => setDialog('delete')}>Delete cycle</Button></div>
                </SettingsCard>
            )}

            <RunDialog cycle={cycle} me={me} open={dialog === 'run'} onClose={() => setDialog(null)}
                onStarted={() => queryClient.invalidateQueries({ queryKey: key })} />
            <ManualDialog cycle={cycle} open={dialog === 'manual'} onClose={() => setDialog(null)}
                onStarted={() => queryClient.invalidateQueries({ queryKey: key })} />
            <AddDialog cycle={cycle} open={dialog === 'add'} onClose={() => setDialog(null)} onChanged={set} />
            <EditDialog cycle={cycle} open={dialog === 'edit'} onClose={() => setDialog(null)} onChanged={set} />
            <Dialog open={dialog === 'delete'} onOpenChange={(o) => !o && setDialog(null)}>
                <DialogContent className="sm:max-w-[440px]">
                    <DialogHeader>
                        <DialogTitle>Delete “{cycle.name}”?</DialogTitle>
                        <DialogDescription>The cycle and its list of tests go. Its {cycle.runs_total} run(s) and any sign-off stay.</DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setDialog(null)}>Keep it</Button>
                        <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete cycle</Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function ItemRow({ item, editable, picked, onToggle }: { item: CycleItem; editable: boolean; picked: boolean; onToggle: () => void }) {
    const link = itemLink(item);
    return (
        <tr className="border-t border-border align-top">
            {editable && (
                <td className="py-2.5">
                    <input type="checkbox" className={checkboxClass} checked={picked} onChange={onToggle} aria-label={`Select ${item.name}`} />
                </td>
            )}
            <th scope="row" className="py-2.5 pr-3 text-left font-normal">
                <span className="font-medium text-ink [overflow-wrap:anywhere]">{item.name}</span>
                <span className="block text-xs text-ink-soft">
                    {[item.suite_name, item.kind === 'manual' ? 'Manual' : 'Automated', item.priority].filter(Boolean).join(' · ')}
                </span>
            </th>
            <td className="py-2.5 pr-3 text-ink">{item.assignee?.name ?? <span className="text-ink-soft">Nobody</span>}</td>
            <td className="py-2.5 pr-3">
                <Pill tone={stateTone[item.status]}>{stateLabel[item.status]}</Pill>
                {item.configurations > 1 && <span className="mt-1 block text-xs text-ink-soft">worst of {item.configurations} browsers</span>}
            </td>
            <td className="py-2.5 text-xs text-ink-soft">
                {link ? (
                    <Link to={link} className="font-medium text-ink underline underline-offset-2">
                        {item.tested_at ? new Date(item.tested_at).toLocaleString() : `Run #${item.run_id}`}
                    </Link>
                ) : '—'}
                {item.tested_by && <span className="block">by {item.tested_by.name}</span>}
            </td>
        </tr>
    );
}

function ScopeFields({ scope, setScope, mine, setMine, idPrefix }: {
    scope: Scope; setScope: (s: Scope) => void; mine: boolean; setMine: (m: boolean) => void; idPrefix: string;
}) {
    return (
        <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[13px] font-semibold text-ink">Which tests</legend>
            {([['remaining', 'Only tests that have not passed yet'], ['all', 'Every test, including the ones that passed']] as [Scope, string][]).map(([v, label]) => (
                <label key={v} className="flex cursor-pointer items-center gap-2.5 text-sm text-ink">
                    <input type="radio" name={`${idPrefix}-scope`} className="h-[18px] w-[18px] accent-ink" checked={scope === v} onChange={() => setScope(v)} />
                    {label}
                </label>
            ))}
            <label className="mt-1 flex cursor-pointer items-center gap-2.5 text-sm text-ink">
                <input type="checkbox" className={checkboxClass} checked={mine} onChange={(e) => setMine(e.target.checked)} />
                Only tests assigned to me
            </label>
        </fieldset>
    );
}

function RunDialog({ cycle, me, open, onClose, onStarted }: { cycle: CycleDetail; me: number | null; open: boolean; onClose: () => void; onStarted: () => void }) {
    const [scope, setScope] = useState<Scope>('remaining');
    const [mine, setMine] = useState(false);
    const plan = automatedRunPlan(cycle.items, { scope, mineOnly: mine, me });
    const total = plan.reduce((n, g) => n + g.caseIds.length, 0);

    const start = useMutation({
        mutationFn: async () => {
            let started = 0;
            const errors: string[] = [];
            // One after the other: a refusal (quota, policy) should stop the rest.
            for (const g of plan) {
                try {
                    const out = await triggerRun(g.suiteId, undefined, undefined, undefined, {
                        case_ids: g.caseIds, cycle_id: cycle.id, git_commit: cycle.git_commit ?? undefined,
                    });
                    started += Array.isArray(out) ? out.length : 1;
                } catch (e) {
                    errors.push(errDetail(e));
                    break;
                }
            }
            return { started, errors };
        },
        onSuccess: ({ started, errors }) => {
            if (started) toast.success(`Started ${started} run${started === 1 ? '' : 's'}`);
            if (errors.length) toast.error(errors[0]);
            onStarted();
            if (started) onClose();
        },
    });

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="sm:max-w-[480px]">
                <DialogHeader>
                    <DialogTitle>Run automated tests</DialogTitle>
                    <DialogDescription>Runs start with each test's usual browsers and settings and count towards this cycle.</DialogDescription>
                </DialogHeader>
                <ScopeFields scope={scope} setScope={setScope} mine={mine} setMine={setMine} idPrefix="auto" />
                <p className="text-sm text-ink" role="status">
                    {total === 0 ? 'Nothing to run with these choices.'
                        : `${total} test${total === 1 ? '' : 's'} in ${plan.length} run${plan.length === 1 ? '' : 's'} (one per top-level suite).`}
                </p>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose}>Cancel</Button>
                    <Button disabled={total === 0 || start.isPending} onClick={() => start.mutate()}>
                        {start.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Start
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function ManualDialog({ cycle, open, onClose, onStarted }: { cycle: CycleDetail; open: boolean; onClose: () => void; onStarted: () => void }) {
    const navigate = useNavigate();
    const [scope, setScope] = useState<Scope>('remaining');
    const [mine, setMine] = useState(true);

    const start = useMutation({
        mutationFn: () => cyclesApi.startManual(cycle.id, { scope, mine_only: mine }),
        onSuccess: (out) => {
            onStarted();
            onClose();
            if (out.runs.length === 1) {
                navigate(`/manual-runs/${out.runs[0].id}`);
            } else if (out.runs.length > 1) {
                toast.success(`Started ${out.runs.length} manual runs — open them under Runs`);
            } else if (out.open_run_ids.length === 1) {
                navigate(`/manual-runs/${out.open_run_ids[0]}`);
            } else {
                toast.message('These tests are already waiting in open manual runs — continue them under Runs');
            }
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="sm:max-w-[480px]">
                <DialogHeader>
                    <DialogTitle>Test manually</DialogTitle>
                    <DialogDescription>
                        Opens a manual run with the steps to follow. A test already waiting in someone's open run is left there.
                    </DialogDescription>
                </DialogHeader>
                <ScopeFields scope={scope} setScope={setScope} mine={mine} setMine={setMine} idPrefix="manual" />
                <DialogFooter>
                    <Button variant="outline" onClick={onClose}>Cancel</Button>
                    <Button disabled={start.isPending} onClick={() => start.mutate()}>
                        {start.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Start testing
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function AddDialog({ cycle, open, onClose, onChanged }: { cycle: CycleDetail; open: boolean; onClose: () => void; onChanged: (d: CycleDetail) => void }) {
    const [suite, setSuite] = useState('');
    const [text, setText] = useState('');
    const suites = useQuery({ queryKey: ['suites', cycle.project_id], queryFn: () => getTestSuites(cycle.project_id), enabled: open });
    const needle = text.trim();
    const found = useQuery({
        queryKey: ['cycle-case-search', cycle.project_id, needle],
        queryFn: () => cyclesApi.searchCases(cycle.project_id, needle),
        enabled: open && needle.length >= 2,
    });
    const inCycle = new Set(cycle.items.map((i) => i.case_id));

    const add = useMutation({
        mutationFn: (body: { suite_id?: number; case_ids?: number[] }) => cyclesApi.addItems(cycle.id, body),
        onSuccess: (d) => {
            const added = d.items.length - cycle.items.length;
            toast.success(added > 0 ? `Added ${added} test${added === 1 ? '' : 's'}` : 'Those tests were already in the cycle');
            onChanged(d);
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="sm:max-w-[520px]">
                <DialogHeader>
                    <DialogTitle>Add tests</DialogTitle>
                    <DialogDescription>Bring in a whole suite, or find single tests by name.</DialogDescription>
                </DialogHeader>
                <div className="flex flex-col gap-1.5">
                    <label htmlFor="add-suite" className="text-[13px] font-semibold text-ink">A suite and everything inside it</label>
                    <div className="flex gap-2">
                        <select id="add-suite" className={selectClass} value={suite} onChange={(e) => setSuite(e.target.value)}>
                            <option value="">Choose a suite…</option>
                            {(suites.data ?? []).map((s: { id: number; name: string }) => <option key={s.id} value={s.id}>{s.name}</option>)}
                        </select>
                        <Button disabled={!suite || add.isPending} onClick={() => add.mutate({ suite_id: Number(suite) })}>Add</Button>
                    </div>
                </div>
                <div className="flex flex-col gap-1.5">
                    <label htmlFor="add-search" className="text-[13px] font-semibold text-ink">Single tests</label>
                    <Input id="add-search" className={inputClass} value={text} placeholder="Type part of a test's name" onChange={(e) => setText(e.target.value)} />
                    {needle.length >= 2 && (
                        found.isLoading ? <p role="status" className="text-sm text-ink-soft">Searching…</p>
                            : (found.data ?? []).length === 0 ? <p className="text-sm text-ink-soft">No test with that name.</p>
                                : (
                                    <ul className="max-h-64 overflow-y-auto rounded-2xl border border-border">
                                        {(found.data ?? []).map((c) => (
                                            <li key={c.id} className="flex min-h-11 items-center justify-between gap-3 border-b border-[#F0EEE8] px-4 py-1.5 text-sm last:border-b-0">
                                                <span className="min-w-0 text-ink [overflow-wrap:anywhere]">{c.name}
                                                    <span className="ml-2 text-xs text-ink-soft">{c.executor === 'manual' ? 'Manual' : 'Automated'}</span>
                                                </span>
                                                {inCycle.has(c.id) ? <span className="text-xs text-ink-soft">In the cycle</span>
                                                    : <Button size="sm" variant="outline" disabled={add.isPending} onClick={() => add.mutate({ case_ids: [c.id] })}>Add</Button>}
                                            </li>
                                        ))}
                                    </ul>
                                )
                    )}
                </div>
                <DialogFooter><Button variant="outline" onClick={onClose}>Done</Button></DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function EditDialog({ cycle, open, onClose, onChanged }: { cycle: CycleDetail; open: boolean; onClose: () => void; onChanged: (d: CycleDetail) => void }) {
    const [name, setName] = useState(cycle.name);
    const [release, setRelease] = useState(cycle.release_label ?? '');
    const [commit, setCommit] = useState(cycle.git_commit ?? '');
    const [due, setDue] = useState(cycle.due_date ?? '');
    const [description, setDescription] = useState(cycle.description ?? '');

    const save = useMutation({
        mutationFn: () => cyclesApi.update(cycle.id, {
            name: name.trim(), release_label: release.trim() || null, git_commit: commit.trim() || null,
            due_date: due || null, description: description.trim() || null,
        }),
        onSuccess: (d) => { onChanged(d); onClose(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const submit = (e: FormEvent) => { e.preventDefault(); if (name.trim()) save.mutate(); };

    return (
        <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
            <DialogContent className="sm:max-w-[520px]">
                <DialogHeader><DialogTitle>Edit cycle</DialogTitle></DialogHeader>
                <form onSubmit={submit} className="flex flex-col gap-4">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="edit-name" className="text-[13px] font-semibold text-ink">Name</label>
                        <Input id="edit-name" className={inputClass} value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="edit-release" className="text-[13px] font-semibold text-ink">Release</label>
                            <Input id="edit-release" className={inputClass} value={release} maxLength={120} onChange={(e) => setRelease(e.target.value)} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="edit-due" className="text-[13px] font-semibold text-ink">Due</label>
                            <Input id="edit-due" type="date" className={inputClass} value={due} onChange={(e) => setDue(e.target.value)} />
                        </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="edit-commit" className="text-[13px] font-semibold text-ink">Commit <span className="font-normal text-ink-soft">(optional)</span></label>
                        <Input id="edit-commit" className={`${inputClass} font-mono`} value={commit} maxLength={64} onChange={(e) => setCommit(e.target.value)} />
                        <p className="text-xs text-ink-soft">Runs started from this cycle are stamped with it.</p>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="edit-description" className="text-[13px] font-semibold text-ink">Notes</label>
                        <textarea id="edit-description" className={`${textareaClass} font-sans`} rows={3} maxLength={4000}
                            value={description} onChange={(e) => setDescription(e.target.value)} />
                    </div>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
                        <Button type="submit" disabled={!name.trim() || save.isPending}>Save</Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}

function SignoffCard({ cycle, onSigned }: { cycle: CycleDetail; onSigned: () => void }) {
    const [verdict, setVerdict] = useState<SignoffVerdict>('approve');
    const [note, setNote] = useState('');
    const remaining = cycle.items.filter((i) => isRemaining(i.status)).length;
    const label = cycle.release_label || cycle.name;

    const sign = useMutation({
        mutationFn: () => manualTestingApi.signOff(cycle.project_id, {
            release_label: label, run_ids: [], cycle_id: cycle.id, verdict, note: note.trim() || undefined,
            git_commit: cycle.git_commit ?? undefined,
        }),
        onSuccess: (s) => { toast.success(s.verdict === 'approve' ? 'Release approved' : 'Release rejected'); setNote(''); onSigned(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const s = cycle.signoff;

    return (
        <SettingsCard
            labelledBy="cycle-signoff"
            title="Sign-off"
            icon={<ShieldCheck className="h-4 w-4" aria-hidden="true" />}
            description="A person's go or no-go for this release, recorded against the runs that hold each test's current result. Every sign-off is audited."
            footer={cycle.can_edit ? (
                <>
                    <Button onClick={() => sign.mutate()} disabled={sign.isPending || cycle.progress.executed === 0}>
                        {sign.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                        {verdict === 'approve' ? 'Approve release' : 'Reject release'}
                    </Button>
                    <span className="text-xs text-ink-soft" role="status">
                        {cycle.progress.executed === 0 ? 'Nothing has been tested yet.'
                            : remaining > 0 ? `${remaining} test${remaining === 1 ? ' has' : 's have'} not passed — say in the note why that is acceptable.` : 'Every test has passed.'}
                    </span>
                </>
            ) : undefined}
        >
            {s ? (
                <div className="flex flex-wrap items-start gap-3 rounded-2xl bg-background px-4 py-3">
                    <Pill tone={s.verdict === 'approve' ? 'mint' : 'peach'}>{s.verdict === 'approve' ? 'Approved' : 'Rejected'}</Pill>
                    <div className="min-w-0 flex-1 text-sm">
                        <span className="text-ink">{s.signed_by?.name ?? 'A former member'} · {new Date(s.signed_at).toLocaleString()}</span>
                        {s.note && <p className="mt-1 whitespace-pre-wrap text-ink [overflow-wrap:anywhere]">{s.note}</p>}
                    </div>
                    <Link to="/release-signoff" className="text-sm font-medium text-ink underline underline-offset-2">History</Link>
                </div>
            ) : <p className="text-sm text-ink-soft">Not signed off yet.</p>}
            {cycle.can_edit && (
                <div className="flex flex-col gap-3">
                    <div role="radiogroup" aria-label="Verdict" className="flex gap-2">
                        {(['approve', 'reject'] as SignoffVerdict[]).map((v) => (
                            <button key={v} type="button" role="radio" aria-checked={verdict === v} onClick={() => setVerdict(v)}
                                className={cn('h-10 rounded-full border px-4 text-sm font-medium',
                                    verdict === v ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-background')}>
                                {v === 'approve' ? 'Approve' : 'Reject'}
                            </button>
                        ))}
                    </div>
                    <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                        Note
                        <textarea className={`${textareaClass} font-sans`} rows={2} maxLength={5000} value={note} onChange={(e) => setNote(e.target.value)}
                            placeholder="Known issues accepted, scope, conditions" />
                    </label>
                </div>
            )}
        </SettingsCard>
    );
}
