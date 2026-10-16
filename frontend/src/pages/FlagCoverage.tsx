import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EmptyState, PageHeader, PanelTabs, Pill } from '@/components/redesign/primitives';
import { FieldRow, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { Card, DataTable, Loading, StatTile } from '@/components/journey-reports/parts';
import { errDetail, parseList, shortDate } from '@/components/journey-reports/helpers';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    createFlag, deleteFlag, getFlagCoverage, listFlagCatalog, updateFlag, type FlagCatalogEntry,
} from '@/api/journeyReports';

type Tab = 'coverage' | 'catalog';
const WINDOWS = [7, 30, 90, 180];

/** Which feature flags / A-B variants the runs exercised, against the catalogue. */
export default function FlagCoverage() {
    const projectId = useActiveProjectId();
    const [tab, setTab] = useState<Tab>('coverage');
    return (
        <div className="flex flex-col gap-6">
            <PageHeader
                title="Flag coverage"
                subtitle={<>Runs declare the flags and variants they exercised (<code>flags</code> in the <code>POST /api/runs</code> body, from CI or the API); steps can read them as <code>{'{{flag.NAME}}'}</code>.</>}
                toolbar={projectId ? (
                    <PanelTabs label="Flag coverage views" value={tab} onChange={setTab}
                        tabs={[{ id: 'coverage', label: 'Coverage' }, { id: 'catalog', label: 'Flag catalogue' }]} />
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="No project selected">Select a project in the sidebar.</EmptyState>
            ) : tab === 'coverage' ? <CoverageView projectId={projectId} /> : <CatalogView projectId={projectId} />}
        </div>
    );
}

function CoverageView({ projectId }: { projectId: number }) {
    const [days, setDays] = useState(30);
    const q = useQuery({ queryKey: ['flag-coverage', projectId, days], queryFn: () => getFlagCoverage(projectId, days) });
    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-3">
                <label htmlFor="fc-days" className="text-sm font-medium text-ink">Window</label>
                <select id="fc-days" value={days} onChange={(e) => setDays(Number(e.target.value))}
                    className="h-10 rounded-full border border-input bg-white px-4 text-sm">
                    {WINDOWS.map((d) => <option key={d} value={d}>Last {d} days</option>)}
                </select>
            </div>
            {q.isLoading ? <Loading /> : q.isError || !q.data ? (
                <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>
            ) : (
                <>
                    <div className="flex flex-wrap gap-3">
                        <StatTile label="Runs with flags" value={q.data.runs_with_flags} hint={q.data.truncated ? 'newest 5,000 counted' : `last ${days} days`} />
                        <StatTile label="Flags seen or catalogued" value={q.data.flags.length} />
                        <StatTile label="Catalogue values never tested" value={q.data.never_tested_total} />
                        <StatTile label="Flags not in the catalogue" value={q.data.uncatalogued.length} />
                    </div>
                    {!q.data.flags.length ? (
                        <EmptyState title="No flags yet">No run in this window declared flags, and the catalogue is empty.</EmptyState>
                    ) : q.data.flags.map((f) => (
                        <Card key={f.name}
                            title={<span className="flex flex-wrap items-center gap-2"><code>{f.name}</code>
                                {!f.in_catalog && <Pill tone="lavender">not in catalogue</Pill>}
                                {f.never_tested.length > 0 && <Pill tone="peach">{f.never_tested.length} value(s) never tested</Pill>}</span>}
                            description={f.description || undefined}>
                            {f.never_tested.length > 0 && (
                                <p className="text-sm text-ink">Never tested: {f.never_tested.map((v) => <code key={v} className="mr-2">{v}</code>)}</p>
                            )}
                            {f.values.length ? (
                                <DataTable label={`Values of ${f.name}`} head={['Value', 'Runs', 'Cases', 'Passed', 'Failed', 'Last seen']}>
                                    {f.values.map((v) => (
                                        <tr key={v.value}>
                                            <td className="px-2 py-2 font-mono text-xs">{v.value}
                                                {f.in_catalog && !v.in_catalog && <Pill tone="lavender" className="ml-2">not catalogued</Pill>}</td>
                                            <td className="px-2 py-2">{v.runs}</td>
                                            <td className="px-2 py-2">{v.cases}</td>
                                            <td className="px-2 py-2">{v.passed_runs}</td>
                                            <td className="px-2 py-2">{v.failed_runs}</td>
                                            <td className="px-2 py-2 text-xs">
                                                {shortDate(v.last_seen)}{v.last_run_id && <> · <Link className="underline" to={`/runs/${v.last_run_id}`}>run</Link></>}
                                            </td>
                                        </tr>
                                    ))}
                                </DataTable>
                            ) : <p className="text-sm text-ink-soft">No run in this window used this flag.</p>}
                        </Card>
                    ))}
                </>
            )}
        </div>
    );
}

function CatalogView({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const q = useQuery({ queryKey: ['flag-catalog', projectId], queryFn: () => listFlagCatalog(projectId) });
    const [name, setName] = useState('');
    const [values, setValues] = useState('');
    const [description, setDescription] = useState('');
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['flag-catalog', projectId] });
        qc.invalidateQueries({ queryKey: ['flag-coverage', projectId] });
    };
    const add = useMutation({
        mutationFn: () => createFlag(projectId, { name: name.trim(), values: parseList(values), description: description.trim() || null }),
        onSuccess: () => { toast.success('Flag added'); setName(''); setValues(''); setDescription(''); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <div className="flex flex-col gap-5">
            <Card title="Add a flag" description="Name as runs send it (letters, digits, _ . -). Values are compared as text; booleans are true / false.">
                <FieldRow label="Name" htmlFor="fl-name">
                    <Input id="fl-name" className={`${inputClass} font-mono`} value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="checkout_v2" />
                </FieldRow>
                <FieldRow label="Possible values" htmlFor="fl-values" hint="Comma-separated." wide>
                    <Input id="fl-values" className={inputClass} value={values} onChange={(e) => setValues(e.target.value)} placeholder="true, false" />
                </FieldRow>
                <FieldRow label="Description" htmlFor="fl-desc" wide>
                    <Input id="fl-desc" className={inputClass} value={description} maxLength={1000} onChange={(e) => setDescription(e.target.value)} />
                </FieldRow>
                <div className="flex gap-3 border-t border-border pt-4">
                    <Button onClick={() => add.mutate()} disabled={!name.trim() || add.isPending}>
                        {add.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Add flag
                    </Button>
                </div>
            </Card>
            <Card title="Catalogue" description="Editing needs the editor role on this project.">
                {q.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : !q.data?.flags.length ? (
                    <p className="text-sm text-ink-soft">No flags catalogued yet.</p>
                ) : (
                    <div className="flex flex-col gap-3">
                        {q.data.flags.map((f) => <FlagRow key={`${f.id}-${f.values.join(',')}-${f.description}`} flag={f} onChanged={refresh} />)}
                    </div>
                )}
            </Card>
        </div>
    );
}

function FlagRow({ flag, onChanged }: { flag: FlagCatalogEntry; onChanged: () => void }) {
    const [values, setValues] = useState(flag.values.join(', '));
    const [description, setDescription] = useState(flag.description || '');
    const save = useMutation({
        mutationFn: () => updateFlag(flag.id, { values: parseList(values), description: description.trim() || null }),
        onSuccess: () => { toast.success('Flag saved'); onChanged(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => deleteFlag(flag.id),
        onSuccess: () => { toast.success('Flag removed'); onChanged(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <div className="flex flex-col gap-2 rounded-2xl border border-border p-3 md:flex-row md:items-end">
            <span className="font-mono text-sm text-ink md:w-48 md:pb-2">{flag.name}</span>
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium text-ink-soft">Values
                <Input className={inputClass} value={values} onChange={(e) => setValues(e.target.value)} />
            </label>
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium text-ink-soft">Description
                <Input className={inputClass} value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
            <div className="flex gap-2">
                <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
                <Button size="sm" variant="outline" disabled={remove.isPending}
                    onClick={() => { if (window.confirm(`Remove ${flag.name} from the catalogue?`)) remove.mutate(); }}>Remove</Button>
            </div>
        </div>
    );
}
