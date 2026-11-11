import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyState, PageHeader, PanelTabs, Pill } from '@/components/redesign/primitives';
import { inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { getProjects, getTestSuites } from '@/lib/api';
import { errDetail } from '@/lib/errors';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { cyclesApi, type Cycle } from '@/api/cycles';
import { ProgressBar } from '@/components/cycles/ProgressBar';
import { cycleBadge, dueText, progressText } from '@/components/cycles/model';
import { selectClass } from '@/components/cycles/styles';

type Tab = 'open' | 'closed';

/** Test cycles of the active project: one per round of testing for a release. */
export default function TestCycles() {
    const projectId = useActiveProjectId();
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = projects.data?.find((p) => p.id === projectId);
    const [tab, setTab] = useState<Tab>('open');
    const [creating, setCreating] = useState(false);

    const cycles = useQuery({
        queryKey: ['test-cycles', projectId],
        queryFn: () => cyclesApi.list(projectId!),
        enabled: !!projectId,
    });

    if (!projectId) {
        return (
            <div className="flex flex-col gap-5">
                <PageHeader title="Test cycles" subtitle="Select a project in the sidebar." />
            </div>
        );
    }

    const all = cycles.data ?? [];
    const shown = all.filter((c) => c.status === tab);
    const count = (t: Tab) => all.filter((c) => c.status === t).length;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Test cycles"
                subtitle={<>Plan a round of testing for a release of <strong className="text-ink">{project?.name ?? 'this project'}</strong>: which tests, who runs them, and how far along it is.</>}
                actions={<Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" aria-hidden="true" /> New cycle</Button>}
                toolbar={
                    <PanelTabs<Tab> label="Cycles" value={tab} onChange={setTab} tabs={[
                        { id: 'open', label: 'Open', count: count('open') || undefined },
                        { id: 'closed', label: 'Closed', count: count('closed') || undefined },
                    ]} />
                }
            />

            {cycles.isLoading ? (
                <p role="status" className="flex items-center gap-2 text-sm text-ink-soft"><Loader2 className="h-4 w-4 animate-spin" /> Loading…</p>
            ) : cycles.error ? (
                <EmptyState title="Could not load the cycles">{errDetail(cycles.error)}</EmptyState>
            ) : shown.length === 0 ? (
                <EmptyState title={tab === 'open' ? 'No open cycles' : 'No closed cycles'}>
                    {tab === 'open'
                        ? 'A cycle gathers the manual and automated tests for one release, shows who is testing what, and ends with a sign-off.'
                        : 'Cycles you close are kept here with their results.'}
                </EmptyState>
            ) : (
                <ul className="grid gap-4 lg:grid-cols-2">
                    {shown.map((c) => <li key={c.id}><CycleCard cycle={c} /></li>)}
                </ul>
            )}

            <NewCycleDialog projectId={projectId} open={creating} onOpenChange={setCreating} />
        </div>
    );
}

function CycleCard({ cycle }: { cycle: Cycle }) {
    const badge = cycleBadge(cycle);
    const due = cycle.status === 'open' ? dueText(cycle.due_date) : null;
    return (
        <Link to={`/test-cycles/${cycle.id}`}
            className="flex h-full flex-col gap-3 rounded-3xl border border-border bg-white p-5 hover:border-ink/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                    <h2 className="text-base font-semibold text-ink [overflow-wrap:anywhere]">{cycle.name}</h2>
                    <p className="text-[13px] text-ink-soft">
                        {[cycle.release_label && `Release ${cycle.release_label}`, due,
                            `${cycle.manual_cases} manual · ${cycle.automated_cases} automated`].filter(Boolean).join(' · ')}
                    </p>
                </div>
                <Pill tone={badge.tone}>{badge.label}</Pill>
            </div>
            <ProgressBar progress={cycle.progress} />
            <p className="text-[13px] text-ink-soft">{progressText(cycle.progress)}</p>
        </Link>
    );
}

function NewCycleDialog({ projectId, open, onOpenChange }: { projectId: number; open: boolean; onOpenChange: (o: boolean) => void }) {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [name, setName] = useState('');
    const [release, setRelease] = useState('');
    const [due, setDue] = useState('');
    const [suite, setSuite] = useState('');
    const [description, setDescription] = useState('');

    const suites = useQuery({
        queryKey: ['suites', projectId],
        queryFn: () => getTestSuites(projectId),
        enabled: open,
    });

    const create = useMutation({
        mutationFn: () => cyclesApi.create(projectId, {
            name: name.trim(), release_label: release.trim() || null, due_date: due || null,
            description: description.trim() || null, suite_id: suite ? Number(suite) : undefined,
        }),
        onSuccess: (c) => {
            queryClient.invalidateQueries({ queryKey: ['test-cycles', projectId] });
            onOpenChange(false);
            setName(''); setRelease(''); setDue(''); setSuite(''); setDescription('');
            navigate(`/test-cycles/${c.id}`);
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    const submit = (e: FormEvent) => { e.preventDefault(); if (name.trim()) create.mutate(); };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-[520px]">
                <DialogHeader>
                    <DialogTitle>New test cycle</DialogTitle>
                    <DialogDescription>You can add or remove tests and assign people afterwards.</DialogDescription>
                </DialogHeader>
                <form onSubmit={submit} className="flex flex-col gap-4">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="cycle-name" className="text-[13px] font-semibold text-ink">Name</label>
                        <Input id="cycle-name" className={inputClass} value={name} maxLength={120} required autoFocus
                            placeholder="Release 2.4 regression" onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="cycle-release" className="text-[13px] font-semibold text-ink">Release <span className="font-normal text-ink-soft">(optional)</span></label>
                            <Input id="cycle-release" className={inputClass} value={release} maxLength={120}
                                placeholder="2.4.0" onChange={(e) => setRelease(e.target.value)} />
                        </div>
                        <div className="flex flex-col gap-1.5">
                            <label htmlFor="cycle-due" className="text-[13px] font-semibold text-ink">Due <span className="font-normal text-ink-soft">(optional)</span></label>
                            <Input id="cycle-due" type="date" className={inputClass} value={due} onChange={(e) => setDue(e.target.value)} />
                        </div>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="cycle-suite" className="text-[13px] font-semibold text-ink">Start with the tests of</label>
                        <select id="cycle-suite" className={selectClass} value={suite} onChange={(e) => setSuite(e.target.value)}>
                            <option value="">No tests yet — I will add them</option>
                            {(suites.data ?? []).map((s: { id: number; name: string }) => (
                                <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                        </select>
                        <p className="text-xs text-ink-soft">Takes every test in that suite and the suites inside it.</p>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="cycle-description" className="text-[13px] font-semibold text-ink">Notes <span className="font-normal text-ink-soft">(optional)</span></label>
                        <textarea id="cycle-description" className={`${textareaClass} font-sans`} rows={3} maxLength={4000}
                            value={description} onChange={(e) => setDescription(e.target.value)}
                            placeholder="Scope, environment, what is out of scope" />
                    </div>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
                        <Button type="submit" disabled={!name.trim() || create.isPending}>
                            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Create cycle
                        </Button>
                    </DialogFooter>
                </form>
            </DialogContent>
        </Dialog>
    );
}
