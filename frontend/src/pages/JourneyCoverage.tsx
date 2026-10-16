import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { EmptyState, PageHeader, PanelTabs, Pill } from '@/components/redesign/primitives';
import { FieldRow, inputClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';
import { Card, DataTable, Loading, StatTile } from '@/components/journey-reports/parts';
import { errDetail, formatViews, pct, shortDate } from '@/components/journey-reports/helpers';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import {
    deleteJourneyImport, getJourneyCoverage, getJourneyRules, listJourneyImports, putJourneyRules, uploadJourneyImport,
    type CoverageEntry, type CoverageResponse, type PathRule, type RuleKind,
} from '@/api/journeyReports';

type Tab = 'coverage' | 'imports' | 'rules';

/**
 * Journey coverage map: real traffic from an analytics export (generic CSV or
 * JSON) against the pages the project's test cases visit.
 */
export default function JourneyCoverage() {
    const projectId = useActiveProjectId();
    const [tab, setTab] = useState<Tab>('coverage');
    const [importId, setImportId] = useState<number | null>(null);
    return (
        <div className="flex flex-col gap-6">
            <PageHeader
                title="Journey coverage"
                subtitle="How much of your real traffic the test cases visit, weighted by page views. Import a page-view export from any analytics tool."
                toolbar={projectId ? (
                    <PanelTabs label="Journey coverage views" value={tab} onChange={setTab}
                        tabs={[{ id: 'coverage', label: 'Coverage' }, { id: 'imports', label: 'Imports' }, { id: 'rules', label: 'Path rules' }]} />
                ) : undefined}
            />
            {!projectId ? (
                <EmptyState title="No project selected">Select a project in the sidebar.</EmptyState>
            ) : tab === 'coverage' ? (
                <CoverageView projectId={projectId} importId={importId} onClearImport={() => setImportId(null)} onImport={() => setTab('imports')} />
            ) : tab === 'imports' ? (
                <ImportsView projectId={projectId} onView={(id) => { setImportId(id); setTab('coverage'); }} />
            ) : (
                <RulesView projectId={projectId} />
            )}
        </div>
    );
}

function CoverageView({ projectId, importId, onClearImport, onImport }: {
    projectId: number; importId: number | null; onClearImport: () => void; onImport: () => void;
}) {
    const q = useQuery({
        queryKey: ['journey-coverage', projectId, importId],
        queryFn: () => getJourneyCoverage(projectId, importId),
    });
    if (q.isLoading) return <Loading>Loading coverage…</Loading>;
    if (q.isError || !q.data) return <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>;
    const { report, import: imp } = q.data;
    if (!report || !imp) {
        return (
            <EmptyState title="No traffic imported yet">
                Upload a page-view export (columns: path or url, views; optional journey, step, date) to see coverage.
                <span className="mt-3 block"><Button onClick={onImport}>Import traffic data</Button></span>
            </EmptyState>
        );
    }
    return (
        <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-3 text-sm text-ink-soft">
                <span>
                    Import <strong className="text-ink">{imp.label || imp.filename || `#${imp.id}`}</strong>
                    {imp.period_start && <> · {imp.period_start} to {imp.period_end}</>} · uploaded {shortDate(imp.created_at)}
                </span>
                {importId && <Button size="sm" variant="outline" onClick={onClearImport}>Show latest import</Button>}
            </div>
            <div className="flex flex-wrap gap-3">
                <StatTile label="Traffic covered" value={pct(report.coverage_pct)}
                    hint={`${formatViews(report.covered_views)} of ${formatViews(report.total_views)} views`} />
                <StatTile label="Pages covered" value={`${report.covered_keys} / ${report.distinct_keys}`} hint="after path rules" />
                <StatTile label="Pages tests visit" value={report.tested_keys} hint="distinct goto targets" />
                <StatTile label="Journeys" value={report.journeys.length} />
            </div>
            <div className="grid gap-5 xl:grid-cols-2">
                <Card title="Top uncovered pages" description="Pages with the most traffic that no test case visits.">
                    <EntryTable entries={report.top_uncovered} names={q.data.case_names} empty="Every page with traffic is visited by a test." />
                </Card>
                <Card title="Tested, little or no traffic"
                    description={`Pages tests visit that get under ${report.low_traffic_share_pct}% of views, or none at all.`}>
                    <EntryTable entries={report.covered_low_traffic} names={q.data.case_names} showCases empty="Nothing here." />
                </Card>
            </div>
            <JourneysCard data={q.data} />
            <Card title="Covered pages" description="Pages tests visit, by traffic.">
                <EntryTable entries={report.covered} names={q.data.case_names} showCases empty="No test case visits a page from this import." />
            </Card>
        </div>
    );
}

function EntryTable({ entries, names, showCases = false, empty }: {
    entries: CoverageEntry[]; names?: Record<string, string>; showCases?: boolean; empty: string;
}) {
    if (!entries.length) return <p className="text-sm text-ink-soft">{empty}</p>;
    return (
        <DataTable label="Pages" head={['Page', 'Views', 'Share', ...(showCases ? ['Cases'] : [])]}>
            {entries.map((e) => (
                <tr key={e.key}>
                    <td className="max-w-[320px] break-all px-2 py-2 font-mono text-xs text-ink">
                        {e.key}{e.paths > 1 && <span className="ml-1 text-ink-soft">({e.paths} paths)</span>}
                    </td>
                    <td className="px-2 py-2">{formatViews(e.views)}</td>
                    <td className="px-2 py-2">{pct(e.share_pct)}</td>
                    {showCases && (
                        <td className="px-2 py-2 text-xs text-ink-soft">
                            {e.case_ids.slice(0, 3).map((id) => names?.[String(id)] || `#${id}`).join(', ')}
                            {e.case_ids.length > 3 && ` +${e.case_ids.length - 3}`}
                        </td>
                    )}
                </tr>
            ))}
        </DataTable>
    );
}

function JourneysCard({ data }: { data: CoverageResponse }) {
    const journeys = data.report?.journeys || [];
    return (
        <Card title="Journeys and funnels" description="Rows of the import that name a journey, in step order.">
            {!journeys.length ? (
                <p className="text-sm text-ink-soft">This import has no journey column.</p>
            ) : (
                <div className="flex flex-col gap-4">
                    {journeys.map((j) => (
                        <div key={j.journey} className="rounded-2xl border border-border p-4">
                            <div className="mb-3 flex flex-wrap items-center gap-2">
                                <h3 className="text-sm font-semibold text-ink">{j.journey}</h3>
                                <Pill tone={j.covered_steps === j.step_count ? 'mint' : j.covered_steps ? 'butter' : 'peach'}>
                                    {j.covered_steps} of {j.step_count} steps tested
                                </Pill>
                                <span className="text-xs text-ink-soft">{pct(j.weighted_coverage_pct)} of {formatViews(j.views)} views</span>
                                {j.first_gap && <span className="text-xs text-ink-soft">first gap: <code>{j.first_gap}</code></span>}
                            </div>
                            <ol className="flex flex-wrap gap-2">
                                {j.steps.map((s, i) => (
                                    <li key={s.key} className="flex items-center gap-2 rounded-full border border-border px-3 py-1 text-xs">
                                        <span className="font-mono text-ink-soft">{s.step ?? i + 1}</span>
                                        <span className="font-mono text-ink">{s.key}</span>
                                        <Pill tone={s.covered ? 'mint' : 'peach'}>{s.covered ? 'tested' : 'untested'}</Pill>
                                    </li>
                                ))}
                            </ol>
                        </div>
                    ))}
                </div>
            )}
        </Card>
    );
}

function ImportsView({ projectId, onView }: { projectId: number; onView: (id: number) => void }) {
    const qc = useQueryClient();
    const fileRef = useRef<HTMLInputElement>(null);
    const [file, setFile] = useState<File | null>(null);
    const [label, setLabel] = useState('');
    const [format, setFormat] = useState<'' | 'csv' | 'json'>('');
    const list = useQuery({ queryKey: ['journey-imports', projectId], queryFn: () => listJourneyImports(projectId) });
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['journey-imports', projectId] });
        qc.invalidateQueries({ queryKey: ['journey-coverage', projectId] });
    };
    const upload = useMutation({
        mutationFn: () => uploadJourneyImport(projectId, file as File, { label, format }),
        onSuccess: (imp) => {
            toast.success(`Imported ${imp.row_count} rows (${formatViews(imp.total_views)} views)`);
            setFile(null); setLabel('');
            if (fileRef.current) fileRef.current.value = '';
            refresh();
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: (id: number) => deleteJourneyImport(id),
        onSuccess: () => { toast.success('Import deleted'); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const limits = list.data?.limits;
    return (
        <div className="flex flex-col gap-5">
            <Card title="Import traffic data"
                description={<>CSV or JSON with a <code>path</code> (or <code>url</code>) column and <code>views</code>; optional <code>journey</code>, <code>step</code> and <code>date</code>. Query strings, fragments and hosts are ignored.{limits && <> Up to {Math.round(limits.max_bytes / 1048576)} MB and {limits.max_rows.toLocaleString()} rows; the newest {limits.kept} imports are kept.</>}</>}>
                <FieldRow label="File" htmlFor="ji-file">
                    <input id="ji-file" ref={fileRef} type="file" accept=".csv,.tsv,.txt,.json,text/csv,application/json"
                        onChange={(e) => setFile(e.target.files?.[0] || null)} className="text-sm" />
                </FieldRow>
                <FieldRow label="Label" htmlFor="ji-label" hint="Optional, e.g. the month the data covers.">
                    <Input id="ji-label" className={inputClass} value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} />
                </FieldRow>
                <FieldRow label="Format" htmlFor="ji-format">
                    <select id="ji-format" value={format} onChange={(e) => setFormat(e.target.value as '' | 'csv' | 'json')}
                        className="h-10 w-full rounded-full border border-input bg-white px-4 text-sm">
                        <option value="">Detect from the file</option>
                        <option value="csv">CSV</option>
                        <option value="json">JSON</option>
                    </select>
                </FieldRow>
                <div className="flex gap-3 border-t border-border pt-4">
                    <Button onClick={() => upload.mutate()} disabled={!file || upload.isPending}>
                        {upload.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Upload import
                    </Button>
                </div>
            </Card>
            <Card title="Import history" description="The newest import drives the coverage view and the journey board.">
                {list.isLoading ? <p className="text-sm text-ink-soft">Loading…</p> : !list.data?.imports.length ? (
                    <p className="text-sm text-ink-soft">No imports yet.</p>
                ) : (
                    <DataTable label="Imports" head={['Import', 'Uploaded', 'Rows', 'Views', 'Period', '']}>
                        {list.data.imports.map((i, idx) => (
                            <tr key={i.id}>
                                <td className="px-2 py-2">
                                    <span className="font-medium text-ink">{i.label || i.filename || `#${i.id}`}</span>
                                    {idx === 0 && <Pill tone="mint" className="ml-2">latest</Pill>}
                                    {i.warnings.map((w) => <span key={w} className="block text-xs text-ink-soft">{w}</span>)}
                                </td>
                                <td className="px-2 py-2 text-xs">{shortDate(i.created_at)}</td>
                                <td className="px-2 py-2">{i.row_count.toLocaleString()}</td>
                                <td className="px-2 py-2">{formatViews(i.total_views)}</td>
                                <td className="px-2 py-2 text-xs">{i.period_start ? `${i.period_start} to ${i.period_end}` : '—'}</td>
                                <td className="px-2 py-2">
                                    <div className="flex justify-end gap-2">
                                        <Button size="sm" variant="outline" onClick={() => onView(i.id)}>View coverage</Button>
                                        <Button size="sm" variant="outline" disabled={remove.isPending}
                                            onClick={() => { if (window.confirm('Delete this import?')) remove.mutate(i.id); }}>Delete</Button>
                                    </div>
                                </td>
                            </tr>
                        ))}
                    </DataTable>
                )}
            </Card>
        </div>
    );
}

function RulesView({ projectId }: { projectId: number }) {
    const q = useQuery({ queryKey: ['journey-rules', projectId], queryFn: () => getJourneyRules(projectId) });
    if (q.isLoading) return <Loading />;
    if (q.isError || !q.data) return <p role="alert" className="text-sm text-destructive">{errDetail(q.error)}</p>;
    return <RulesEditor key={JSON.stringify(q.data.path_rules)} projectId={projectId} saved={q.data.path_rules} max={q.data.max_rules} />;
}

function RulesEditor({ projectId, saved, max }: { projectId: number; saved: PathRule[]; max: number }) {
    const qc = useQueryClient();
    const [rules, setRules] = useState<PathRule[]>(saved);
    const save = useMutation({
        mutationFn: () => putJourneyRules(projectId, rules.filter((r) => r.pattern.trim())),
        onSuccess: (d) => {
            qc.setQueryData(['journey-rules', projectId], { path_rules: d.path_rules, max_rules: max });
            qc.invalidateQueries({ queryKey: ['journey-coverage', projectId] });
            toast.success('Path rules saved');
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const set = (i: number, patch: Partial<PathRule>) => setRules(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    return (
        <Card title="Path rules"
            description={<>Fold concrete paths into one page, applied to traffic and to the tests' gotos alike. Pattern: <code>{'{id}'}</code> or <code>*</code> = one segment, <code>**</code> = any number, <code>*</code> inside a segment = any characters (e.g. <code>/product/{'{id}'}</code>, <code>/blog/**</code>). Regex rules use RE2 (no lookaround or backreferences). The first matching rule wins.</>}>
            <p className={warnCalloutClass}>Rules apply to every import, including older ones; nothing needs re-uploading.</p>
            <div className="flex flex-col gap-3">
                {rules.map((r, i) => (
                    <div key={i} className="flex flex-col gap-2 rounded-2xl border border-border p-3 sm:flex-row sm:items-end">
                        <label className="flex min-w-0 flex-[2] flex-col gap-1 text-xs font-medium text-ink-soft">Pattern
                            <Input className={`${inputClass} font-mono`} value={r.pattern} onChange={(e) => set(i, { pattern: e.target.value })} placeholder="/product/{id}" />
                        </label>
                        <label className="flex flex-col gap-1 text-xs font-medium text-ink-soft">Kind
                            <select value={r.kind} onChange={(e) => set(i, { kind: e.target.value as RuleKind })}
                                className="h-10 rounded-full border border-input bg-white px-4 text-sm text-ink">
                                <option value="pattern">Path pattern</option>
                                <option value="regex">Regex (RE2)</option>
                            </select>
                        </label>
                        <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs font-medium text-ink-soft">Display name (optional)
                            <Input className={inputClass} value={r.name || ''} onChange={(e) => set(i, { name: e.target.value || undefined })} />
                        </label>
                        <Button variant="outline" onClick={() => setRules(rules.filter((_, j) => j !== i))}>Remove</Button>
                    </div>
                ))}
            </div>
            <div className="flex flex-wrap gap-3 border-t border-border pt-4">
                <Button onClick={() => save.mutate()} disabled={save.isPending}>
                    {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save rules
                </Button>
                <Button variant="outline" disabled={rules.length >= max}
                    onClick={() => setRules([...rules, { pattern: '', kind: 'pattern' }])}>Add rule</Button>
                <Button variant="ghost" onClick={() => setRules(saved)}>Discard changes</Button>
            </div>
        </Card>
    );
}
