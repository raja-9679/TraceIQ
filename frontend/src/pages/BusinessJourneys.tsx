import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { FieldRow, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { Card, Loading, TrendBars } from '@/components/journey-reports/parts';
import {
    HEALTH_LABEL, criticalityTone, errDetail, formatViews, healthTone, moveItem, parseList, pct, shortDate,
} from '@/components/journey-reports/helpers';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { getProjectMembers } from '@/lib/api';
import {
    createJourney, deleteJourney, getJourneyBoard, listJourneyImports, listProjectCases, updateJourney,
    type BusinessJourney, type Criticality, type JourneyInput,
} from '@/api/journeyReports';

const CRITICALITIES: Criticality[] = ['critical', 'high', 'medium', 'low'];

type Member = { id: number; full_name: string | null; email: string; access_level: string };

/** Named business journeys ("Checkout", "Signup") and how each is doing. */
export default function BusinessJourneys() {
    const projectId = useActiveProjectId();
    const [editing, setEditing] = useState<BusinessJourney | 'new' | null>(null);
    return (
        <div className="flex flex-col gap-6">
            <PageHeader
                title="Business journeys"
                subtitle="Group test cases into the journeys the business cares about, and see each one's health, trend, requirements and traffic."
                actions={projectId && !editing ? <Button onClick={() => setEditing('new')}>New journey</Button> : undefined}
            />
            {!projectId ? (
                <EmptyState title="No project selected">Select a project in the sidebar.</EmptyState>
            ) : editing ? (
                <JourneyForm projectId={projectId} journey={editing === 'new' ? null : editing} onDone={() => setEditing(null)} />
            ) : (
                <Board projectId={projectId} onEdit={setEditing} />
            )}
        </div>
    );
}

function Board({ projectId, onEdit }: { projectId: number; onEdit: (j: BusinessJourney) => void }) {
    const q = useQuery({ queryKey: ['journey-board', projectId], queryFn: () => getJourneyBoard(projectId) });
    if (q.isLoading) return <Loading>Loading journeys…</Loading>;
    if (q.isError || !q.data) return <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>;
    if (!q.data.journeys.length) {
        return <EmptyState title="No journeys yet">Create one with New journey and pick the cases that make it up, in order.</EmptyState>;
    }
    const order = (j: BusinessJourney) => CRITICALITIES.indexOf(j.criticality);
    const journeys = [...q.data.journeys].sort((a, b) => order(a) - order(b) || a.name.localeCompare(b.name));
    return (
        <div className="grid gap-5 lg:grid-cols-2">
            {journeys.map((j) => <JourneyCard key={j.id} j={j} windowDays={q.data.window_days} onEdit={() => onEdit(j)} />)}
        </div>
    );
}

function JourneyCard({ j, windowDays, onEdit }: { j: BusinessJourney; windowDays: number; onEdit: () => void }) {
    return (
        <Card
            title={<span className="flex flex-wrap items-center gap-2">{j.name}<Pill tone={healthTone(j.health)}>{HEALTH_LABEL[j.health]}</Pill></span>}
            description={j.description}
            actions={<Button size="sm" variant="outline" onClick={onEdit}>Edit</Button>}
        >
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-soft">
                <Pill tone={criticalityTone(j.criticality)}>{j.criticality}</Pill>
                <span>Owner: {j.owner ? j.owner.name || j.owner.email : 'none'}</span>
                <span>· Last run: {shortDate(j.last_run_at)}</span>
            </div>
            <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="flex flex-wrap gap-2 text-xs">
                    {(['passing', 'failing', 'flaky', 'not_run'] as const).map((k) => j.counts[k] ? (
                        <Pill key={k} tone={healthTone(k)}>{j.counts[k]} {HEALTH_LABEL[k].toLowerCase()}</Pill>
                    ) : null)}
                </div>
                <div className="flex flex-col items-end gap-1">
                    <span className="text-[11px] text-ink-soft">Daily pass rate, 14 days</span>
                    <TrendBars points={j.trend} label={`${j.name} daily pass rate`} />
                </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-2xl bg-background p-3 text-sm">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Requirements</p>
                    {j.requirements.length ? (
                        <>
                            <p className="mb-2 text-ink">{j.requirements_covered} of {j.requirements.length} covered by its cases</p>
                            <ul className="flex flex-wrap gap-1.5">
                                {j.requirements.map((r) => (
                                    <li key={r.ref}><Pill tone={healthTone(r.status)}>{r.ref}{r.title ? ` · ${r.title}` : ''}</Pill></li>
                                ))}
                            </ul>
                        </>
                    ) : <p className="text-ink-soft">None linked.</p>}
                </div>
                <div className="rounded-2xl bg-background p-3 text-sm">
                    <p className="mb-1 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">Traffic</p>
                    {!j.traffic ? <p className="text-ink-soft">No traffic import. <Link className="underline" to="/journey-coverage">Import one</Link>.</p>
                        : j.traffic.source === 'journey' ? (
                            <p className="text-ink">{formatViews(j.traffic.views)} views through the funnel; {j.traffic.covered_steps} of {j.traffic.step_count} steps tested ({pct(j.traffic.weighted_coverage_pct)} of its traffic).</p>
                        ) : (
                            <p className="text-ink">{formatViews(j.traffic.views)} views on its {j.traffic.pages} page(s){j.traffic.share_pct != null && <> ({pct(j.traffic.share_pct)} of all traffic)</>}.</p>
                        )}
                </div>
            </div>
            <ol className="flex flex-col divide-y divide-[#F0EEE8] rounded-2xl border border-border">
                {j.cases.map((c, i) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                        <span className="w-5 font-mono text-xs text-ink-soft">{i + 1}</span>
                        <span className="min-w-0 flex-1 truncate text-ink">{c.name}</span>
                        {c.quarantined && <Pill tone="butter">quarantined</Pill>}
                        <Pill tone={healthTone(c.state)}>{HEALTH_LABEL[c.state]}</Pill>
                        {c.last_run_id && <Link to={`/runs/${c.last_run_id}`} className="text-xs underline underline-offset-2">Last run</Link>}
                    </li>
                ))}
                {!j.cases.length && <li className="px-3 py-2 text-sm text-ink-soft">No cases in this journey.</li>}
            </ol>
            {j.missing_case_ids.length > 0 && (
                <p className="text-xs text-ink-soft">{j.missing_case_ids.length} case(s) no longer exist in this project and are ignored.</p>
            )}
            <p className="text-[11px] text-ink-soft">Health from the last {windowDays} days of results.</p>
        </Card>
    );
}

function JourneyForm({ projectId, journey, onDone }: { projectId: number; journey: BusinessJourney | null; onDone: () => void }) {
    const qc = useQueryClient();
    const cases = useQuery({ queryKey: ['project-cases', projectId], queryFn: () => listProjectCases(projectId) });
    const members = useQuery({
        queryKey: ['project-members', projectId],
        queryFn: async () => (await getProjectMembers(projectId)) as Member[],
    });
    const imports = useQuery({ queryKey: ['journey-imports', projectId], queryFn: () => listJourneyImports(projectId) });
    const [name, setName] = useState(journey?.name || '');
    const [description, setDescription] = useState(journey?.description || '');
    const [ownerId, setOwnerId] = useState<number | null>(journey?.owner_id ?? null);
    const [criticality, setCriticality] = useState<Criticality>(journey?.criticality || 'medium');
    const [caseIds, setCaseIds] = useState<number[]>(journey?.case_ids || []);
    const [refs, setRefs] = useState((journey?.requirement_refs || []).join(', '));
    const [traffic, setTraffic] = useState(journey?.traffic_journey || '');
    const [filter, setFilter] = useState('');
    const caseName = useMemo(() => new Map((cases.data || []).map((c) => [c.id, c.name])), [cases.data]);
    const funnelNames = imports.data?.imports[0]?.journeys || [];

    const done = () => { qc.invalidateQueries({ queryKey: ['journey-board', projectId] }); onDone(); };
    const save = useMutation({
        mutationFn: () => {
            const body: JourneyInput = {
                name: name.trim(), description: description.trim() || null, owner_id: ownerId, criticality,
                case_ids: caseIds, requirement_refs: parseList(refs), traffic_journey: traffic || null,
            };
            return journey ? updateJourney(journey.id, body) : createJourney(projectId, body);
        },
        onSuccess: () => { toast.success('Journey saved'); done(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => deleteJourney((journey as BusinessJourney).id),
        onSuccess: () => { toast.success('Journey deleted'); done(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const available = (cases.data || []).filter((c) => !caseIds.includes(c.id)
        && (!filter || c.name.toLowerCase().includes(filter.toLowerCase()))).slice(0, 50);

    return (
        <Card title={journey ? `Edit ${journey.name}` : 'New journey'} description="Editing needs the editor role on this project.">
            <FieldRow label="Name" htmlFor="bj-name" wide>
                <Input id="bj-name" className={inputClass} value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="Checkout" />
            </FieldRow>
            <FieldRow label="Description" htmlFor="bj-desc" wide>
                <textarea id="bj-desc" className={`${textareaClass} font-sans`} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
            </FieldRow>
            <FieldRow label="Owner" htmlFor="bj-owner" hint="Must have access to this project.">
                <select id="bj-owner" value={ownerId ?? ''} onChange={(e) => setOwnerId(e.target.value ? Number(e.target.value) : null)}
                    className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                    <option value="">No owner</option>
                    {(members.data || []).map((m) => <option key={m.id} value={m.id}>{m.full_name || m.email}</option>)}
                    {journey?.owner && !(members.data || []).some((m) => m.id === journey.owner?.id) && (
                        <option value={journey.owner.id}>{journey.owner.name || journey.owner.email}</option>
                    )}
                </select>
            </FieldRow>
            <FieldRow label="Criticality" htmlFor="bj-crit">
                <select id="bj-crit" value={criticality} onChange={(e) => setCriticality(e.target.value as Criticality)}
                    className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                    {CRITICALITIES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
            </FieldRow>
            <FieldRow label="Requirement refs" htmlFor="bj-refs" hint="Comma-separated, e.g. PROJ-12. Requirements linked to the cases are added automatically." wide>
                <Input id="bj-refs" className={inputClass} value={refs} onChange={(e) => setRefs(e.target.value)} />
            </FieldRow>
            <FieldRow label="Traffic journey" htmlFor="bj-traffic" hint="A journey / funnel name from the latest traffic import. Without one, traffic is the views of the cases' pages.">
                <select id="bj-traffic" value={traffic} onChange={(e) => setTraffic(e.target.value)}
                    className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                    <option value="">Use the cases' pages</option>
                    {funnelNames.map((n) => <option key={n} value={n}>{n}</option>)}
                    {traffic && !funnelNames.includes(traffic) && <option value={traffic}>{traffic}</option>}
                </select>
            </FieldRow>

            <div className="grid gap-4 border-t border-border pt-4 md:grid-cols-2">
                <div className="flex flex-col gap-2">
                    <h3 className="text-sm font-semibold text-ink">Cases in this journey, in order ({caseIds.length})</h3>
                    <ol className="flex flex-col divide-y divide-[#F0EEE8] rounded-2xl border border-border">
                        {caseIds.map((id, i) => (
                            <li key={id} className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
                                <span className="w-5 font-mono text-xs text-ink-soft">{i + 1}</span>
                                <span className="min-w-0 flex-1 truncate">{caseName.get(id) || `Case #${id}`}</span>
                                <Button size="sm" variant="outline" disabled={i === 0} onClick={() => setCaseIds(moveItem(caseIds, i, -1))}>Up</Button>
                                <Button size="sm" variant="outline" disabled={i === caseIds.length - 1} onClick={() => setCaseIds(moveItem(caseIds, i, 1))}>Down</Button>
                                <Button size="sm" variant="outline" onClick={() => setCaseIds(caseIds.filter((x) => x !== id))}>Remove</Button>
                            </li>
                        ))}
                        {!caseIds.length && <li className="px-3 py-2 text-sm text-ink-soft">Add cases from the list.</li>}
                    </ol>
                </div>
                <div className="flex flex-col gap-2">
                    <label htmlFor="bj-filter" className="text-sm font-semibold text-ink">Add cases</label>
                    <Input id="bj-filter" className={inputClass} value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by name" />
                    <ul className="flex max-h-72 flex-col divide-y divide-[#F0EEE8] overflow-y-auto rounded-2xl border border-border">
                        {cases.isLoading && <li className="px-3 py-2 text-sm text-ink-soft">Loading cases…</li>}
                        {available.map((c) => (
                            <li key={c.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                                <span className="min-w-0 flex-1 truncate">{c.name}</span>
                                <Button size="sm" variant="outline" onClick={() => setCaseIds([...caseIds, c.id])}>Add</Button>
                            </li>
                        ))}
                        {!cases.isLoading && !available.length && <li className="px-3 py-2 text-sm text-ink-soft">No matching cases.</li>}
                    </ul>
                </div>
            </div>

            <div className="flex flex-wrap gap-3 border-t border-border pt-4">
                <Button onClick={() => save.mutate()} disabled={!name.trim() || save.isPending}>
                    {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save journey
                </Button>
                <Button variant="outline" onClick={onDone}>Cancel</Button>
                {journey && (
                    <Button variant="outline" className="ml-auto" disabled={remove.isPending}
                        onClick={() => { if (window.confirm(`Delete the journey "${journey.name}"? Its cases are not affected.`)) remove.mutate(); }}>
                        Delete journey
                    </Button>
                )}
            </div>
        </Card>
    );
}
