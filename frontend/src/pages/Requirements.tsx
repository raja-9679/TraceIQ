import { useState, useMemo, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    RefreshCw, ExternalLink, Download, Search, Sparkles, Trash2, AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects, getTestSuites } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { ticketsApi } from '@/api/tickets';
import {
    requirementsApi, COVERAGE_LABEL, CoverageStatus, ImportedRequirement, ExternalIssueSummary,
} from '@/api/requirements';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Pill, SplitView,
} from '@/components/redesign/primitives';
import { toneClasses, type Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
type SuiteOption = { id: number; name: string };

const COVERAGE_TONE: Record<CoverageStatus, Tone> = {
    covered: 'mint',
    failing: 'peach',
    untested: 'butter',
    not_covered: 'neutral',
};
// List groups: what needs attention first.
const GROUP_ORDER: CoverageStatus[] = ['failing', 'not_covered', 'untested', 'covered'];

const linkCls = 'font-medium text-ink underline decoration-ink/30 underline-offset-2 hover:decoration-ink';
const fieldLabel = 'text-[13px] font-semibold text-ink';

function CoveragePill({ status }: { status: CoverageStatus }) {
    return <Pill tone={COVERAGE_TONE[status] ?? 'neutral'} className="flex-none">{COVERAGE_LABEL[status]}</Pill>;
}

function Field({ id, label, className, children }: { id: string; label: string; className?: string; children: ReactNode }) {
    return (
        <div className={cn('flex flex-col gap-1.5', className)}>
            <label htmlFor={id} className={fieldLabel}>{label}</label>
            {children}
        </div>
    );
}

function ImportPanel({ projectId, workspaceId }: { projectId: number; workspaceId: number }) {
    const qc = useQueryClient();
    const trackers = useQuery({ queryKey: ['trackers', workspaceId], queryFn: () => ticketsApi.listConfigs(workspaceId) });
    const importable = (trackers.data || []).filter((c) => c.enabled && (c.provider === 'jira' || c.provider === 'linear'));
    // The import API refuses a disabled connection, so those are left out of
    // the picker, but "no connection" would be wrong when one exists.
    const disabled = (trackers.data || []).filter((c) => !c.enabled && (c.provider === 'jira' || c.provider === 'linear'));
    const [configId, setConfigId] = useState<number | null>(null);
    const config = importable.find((c) => c.id === configId) || importable[0];
    const [jql, setJql] = useState('');
    const [teamKey, setTeamKey] = useState('');
    const [query, setQuery] = useState('');
    const [label, setLabel] = useState('');
    const [keys, setKeys] = useState('');
    const [results, setResults] = useState<ExternalIssueSummary[]>([]);
    const [picked, setPicked] = useState<Set<string>>(new Set());

    const search = useMutation({
        mutationFn: () => requirementsApi.search(projectId, config.provider === 'jira'
            ? { config_id: config.id, jql }
            : { config_id: config.id, team_key: teamKey || undefined, query: query || undefined, label: label || undefined }),
        onSuccess: (d) => { setResults(d.issues); setPicked(new Set()); if (!d.issues.length) toast.info('No issues matched'); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const doImport = useMutation({
        mutationFn: (list: string[]) => requirementsApi.import(projectId, config.id, list),
        onSuccess: (d) => {
            toast.success(`Imported ${d.created} new, updated ${d.updated}`);
            if (d.not_found.length) toast.warning(`Not found: ${d.not_found.join(', ')}`);
            setPicked(new Set()); setKeys('');
            qc.invalidateQueries({ queryKey: ['imported-requirements', projectId] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    if (trackers.isLoading) return null;
    if (!config) {
        return (
            <section className="rounded-3xl border border-border bg-white p-6 text-sm text-ink-soft">
                {disabled.length > 0 ? (
                    <>Your {disabled.length === 1 ? `${disabled[0].provider === 'linear' ? 'Linear' : 'Jira'} connection is` : 'Jira/Linear connections are'} turned off. Enable {disabled.length === 1 ? 'it' : 'one'} on the <Link to="/issue-trackers" className={linkCls}>Issue trackers</Link> page to import from it.</>
                ) : (
                    <>No Jira or Linear connection in this workspace yet. Add one on the <Link to="/issue-trackers" className={linkCls}>Issue trackers</Link> page.</>
                )}
            </section>
        );
    }
    const toggle = (k: string) => setPicked((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
    const keyList = keys.split(/[\s,]+/).map((k) => k.trim()).filter(Boolean);

    return (
        <section aria-label="Import requirements" className="flex flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <h2 className="text-base font-semibold text-ink">Import from Jira or Linear</h2>
            <div className="flex flex-wrap items-end gap-3">
                <Field id="req-tracker" label="Connection">
                    <Select value={config.id.toString()} onValueChange={(v) => { setConfigId(parseInt(v)); setResults([]); }}>
                        <SelectTrigger id="req-tracker" className="h-10 w-[220px] rounded-full"><SelectValue /></SelectTrigger>
                        <SelectContent>{importable.map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name} ({c.provider})</SelectItem>)}</SelectContent>
                    </Select>
                </Field>
                {config.provider === 'jira' ? (
                    <Field id="req-jql" label="JQL" className="min-w-[260px] flex-1">
                        <Input id="req-jql" className="h-10 font-mono text-xs" placeholder='project = SHOP AND sprint in openSprints()' value={jql} onChange={(e) => setJql(e.target.value)} />
                    </Field>
                ) : (
                    <>
                        <Field id="req-team" label="Team key">
                            <Input id="req-team" className="h-10 w-28" placeholder="ENG" value={teamKey} onChange={(e) => setTeamKey(e.target.value)} />
                        </Field>
                        <Field id="req-query" label="Title contains">
                            <Input id="req-query" className="h-10 w-48" value={query} onChange={(e) => setQuery(e.target.value)} />
                        </Field>
                        <Field id="req-label" label="Label">
                            <Input id="req-label" className="h-10 w-36" value={label} onChange={(e) => setLabel(e.target.value)} />
                        </Field>
                    </>
                )}
                <Button onClick={() => search.mutate()} disabled={search.isPending || (config.provider === 'jira' && !jql.trim())}>
                    <Search className="h-4 w-4" aria-hidden /> Search
                </Button>
            </div>

            {results.length > 0 && (
                <div className="max-h-[320px] divide-y divide-[#F0EEE8] overflow-y-auto rounded-2xl border border-border">
                    {results.map((i) => (
                        <label key={i.key} className="flex cursor-pointer items-center gap-3 px-4 py-2.5 text-sm hover:bg-background">
                            <Checkbox checked={picked.has(i.key)} onCheckedChange={() => toggle(i.key)} aria-label={`Select ${i.key}`} />
                            <span className="w-24 shrink-0 font-mono text-xs text-ink-soft">{i.key}</span>
                            <span className="flex-1 truncate text-ink">{i.title}</span>
                            {i.status && <span className="text-xs text-ink-soft">{i.status}</span>}
                            {i.already_imported && <Pill tone="neutral">imported</Pill>}
                        </label>
                    ))}
                </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
                {results.length > 0 && (
                    <Button variant="outline" disabled={!picked.size || doImport.isPending} onClick={() => doImport.mutate([...picked])}>
                        <Download className="h-4 w-4" aria-hidden /> Import {picked.size || ''} selected
                    </Button>
                )}
                <label htmlFor="req-keys" className="sr-only">Issue keys</label>
                <Input id="req-keys" className="h-10 w-64" placeholder="…or keys: SHOP-12, SHOP-14" value={keys} onChange={(e) => setKeys(e.target.value)} />
                <Button variant="outline" disabled={!keyList.length || doImport.isPending} onClick={() => doImport.mutate(keyList)}>
                    Import by key
                </Button>
            </div>
        </section>
    );
}

function RequirementDetail({ r, projectId }: { r: ImportedRequirement; projectId: number }) {
    const qc = useQueryClient();
    const detail = useQuery({ queryKey: ['requirement', r.id], queryFn: () => requirementsApi.get(r.id) });
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId) });
    const [suiteId, setSuiteId] = useState<string>('');
    const [targetUrl, setTargetUrl] = useState('');
    const [includeAmbiguous, setIncludeAmbiguous] = useState(false);
    const refresh = () => {
        qc.invalidateQueries({ queryKey: ['requirement', r.id] });
        qc.invalidateQueries({ queryKey: ['imported-requirements', projectId] });
    };

    const generate = useMutation({
        mutationFn: () => requirementsApi.generate(r.id, { test_suite_id: parseInt(suiteId), target_url: targetUrl || undefined, include_ambiguous: includeAmbiguous }),
        onSuccess: (d) => {
            const skeletons = d.generated.filter((g) => g.needs_authoring).length;
            toast.success(`${d.generated.length} proposal(s) queued for review${skeletons ? ` (${skeletons} need authoring)` : ''}`);
            if (d.ambiguous.length) toast.warning(`${d.ambiguous.length} ambiguous criterion(s) not generated`);
            refresh();
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const sync = useMutation({ mutationFn: () => requirementsApi.sync(r.id), onSuccess: () => { toast.success('Synced'); refresh(); }, onError: (e) => toast.error(errDetail(e)) });
    const remove = useMutation({ mutationFn: () => requirementsApi.remove(r.id), onSuccess: () => { toast.success('Removed'); refresh(); }, onError: (e) => toast.error(errDetail(e)) });

    return (
        <DetailCard label={`Requirement ${r.external_key}`}>
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-ink-soft">{r.external_key}</span>
                    <CoveragePill status={r.coverage.status} />
                    {r.external_status && <Pill tone="neutral">{r.external_status}</Pill>}
                </div>
                <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{r.title}</h2>
                <p className="text-sm text-ink-soft">
                    {r.criteria_count} criteria · {r.proposed_count} proposed
                    {r.ambiguous_count > 0 && <span className={toneClasses.butter.ink}> · {r.ambiguous_count} ambiguous</span>}
                    {' '}· {r.coverage.cases.length} linked test(s) · synced {new Date(r.last_synced_at + 'Z').toLocaleString()}
                </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
                {r.url && (
                    <Button variant="outline" asChild>
                        <a href={r.url} target="_blank" rel="noreferrer">Open in {r.source} <ExternalLink className="h-4 w-4" aria-hidden /></a>
                    </Button>
                )}
                <Button variant="outline" onClick={() => sync.mutate()} disabled={sync.isPending}>
                    <RefreshCw className={cn('h-4 w-4', sync.isPending && 'animate-spin')} aria-hidden /> Re-sync
                </Button>
                <Button variant="outline" className={toneClasses.peach.ink} onClick={() => { if (confirm(`Remove ${r.external_key}? Linked tests are kept.`)) remove.mutate(); }} disabled={remove.isPending}>
                    <Trash2 className="h-4 w-4" aria-hidden /> Remove
                </Button>
            </div>
            {r.sync_error && (
                <p className="rounded-2xl border border-border px-4 py-3 text-sm text-ink">
                    <Pill tone="peach" className="mr-2">Sync error</Pill>{r.sync_error}
                </p>
            )}

            <div className="flex flex-col gap-2">
                <Eyebrow>Acceptance criteria</Eyebrow>
                {detail.isLoading ? <RefreshCw className="h-4 w-4 animate-spin text-ink-soft" aria-label="Loading" /> : (
                    <ol className="overflow-hidden rounded-2xl border border-border">
                        {(detail.data?.acceptance_criteria || []).map((c, i) => (
                            <li key={c.index} className={cn('flex flex-col gap-1.5 px-4 py-3 text-sm', i > 0 && 'border-t border-[#F0EEE8]')}>
                                <div className="flex items-start gap-3">
                                    <span className="mt-0.5 shrink-0 font-mono text-xs text-ink-soft">AC{c.index + 1}</span>
                                    <pre className="flex-1 whitespace-pre-wrap font-sans text-ink">{c.text}</pre>
                                    {c.proposal_id ? (
                                        <Link to="/proposals" className="shrink-0">
                                            <Pill tone="lavender">#{c.proposal_id} {c.proposal_status}{c.needs_authoring ? ' · needs authoring' : ''}</Pill>
                                        </Link>
                                    ) : null}
                                </div>
                                {c.ambiguous && (
                                    <div className={cn('ml-9 flex items-center gap-1 text-xs', toneClasses.butter.ink)}>
                                        <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> Ambiguous: {c.reasons.join('; ')}
                                    </div>
                                )}
                            </li>
                        ))}
                    </ol>
                )}
            </div>

            <div className="flex flex-col gap-2">
                <Eyebrow>Covered by</Eyebrow>
                {r.coverage.cases.length === 0 ? <p className="text-sm text-ink-soft">No linked tests. Accepting a generated proposal links it automatically.</p> : (
                    <ul className="flex flex-wrap gap-1.5">
                        {r.coverage.cases.map((c) => (
                            <li key={c.id}>
                                <Pill tone="neutral">{c.name} · {c.latest_status ? c.latest_status.toLowerCase() : 'not run'}</Pill>
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            <div className="flex flex-col gap-3 rounded-2xl border border-border p-4">
                <Eyebrow>Generate tests from the criteria</Eyebrow>
                <div className="flex flex-wrap items-end gap-3">
                    <Field id={`gen-suite-${r.id}`} label="Target suite">
                        <Select value={suiteId} onValueChange={setSuiteId}>
                            <SelectTrigger id={`gen-suite-${r.id}`} className="h-10 w-[200px] rounded-full"><SelectValue placeholder="Choose a suite" /></SelectTrigger>
                            <SelectContent>{((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </Field>
                    <Field id={`gen-url-${r.id}`} label="Target URL (optional)" className="min-w-[200px] flex-1">
                        <Input id={`gen-url-${r.id}`} className="h-10" placeholder="https://staging.example.com/checkout" value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} />
                    </Field>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    <label className="flex h-10 items-center gap-2 text-sm text-ink">
                        <Checkbox checked={includeAmbiguous} onCheckedChange={(v) => setIncludeAmbiguous(!!v)} /> Include ambiguous
                    </label>
                    <span className="flex-1" />
                    <Button disabled={!suiteId || generate.isPending} onClick={() => generate.mutate()}>
                        <Sparkles className="h-4 w-4" aria-hidden /> {generate.isPending ? 'Generating…' : 'Generate tests'}
                    </Button>
                </div>
            </div>
        </DetailCard>
    );
}

function Tile({ label, value, tone }: { label: string; value: number; tone: Tone }) {
    return (
        <div className="flex flex-col gap-2 rounded-3xl border border-border bg-white px-5 py-4">
            <span className="flex items-center gap-2 text-[13px] text-ink-soft">
                <span className={cn('h-2 w-2 rounded-full', toneClasses[tone].chip)} aria-hidden />{label}
            </span>
            <span className="text-[28px] font-bold leading-none tracking-tight tabular-nums text-ink">{value}</span>
        </div>
    );
}

export default function Requirements() {
    const qc = useQueryClient();
    const projectId = useActiveProjectId();
    // Only needed for the workspace the tracker connections live in.
    const projects = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = useMemo(() => (projects.data || []).find((p) => p.id === projectId), [projects.data, projectId]);
    const [importing, setImporting] = useState(false);
    const [selectedId, setSelectedId] = useState<number | null>(null);

    const list = useQuery({ queryKey: ['imported-requirements', projectId], queryFn: () => requirementsApi.list(projectId!), enabled: !!projectId });
    const syncAll = useMutation({
        mutationFn: () => requirementsApi.syncAll(projectId!),
        onSuccess: (d) => {
            const errs = Object.keys(d.errors).length;
            if (errs) toast.warning(`Synced ${d.synced}; ${errs} failed`); else toast.success(`Synced ${d.synced}`);
            qc.invalidateQueries({ queryKey: ['imported-requirements', projectId] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    const totals = list.data?.totals;
    const reqs = list.data?.requirements ?? [];
    const current = reqs.find((r) => r.id === selectedId) ?? reqs[0] ?? null;
    const ready = !!projectId && !!project;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Requirements"
                subtitle={
                    'Import tickets from Jira or Linear, turn each acceptance criterion into a test proposal, and see which ' +
                    'requirements are covered, failing or untested. Vague criteria are flagged instead of guessed at.'
                }
                actions={ready ? (
                    <>
                        <Button variant="outline" onClick={() => syncAll.mutate()} disabled={syncAll.isPending || !reqs.length}>
                            <RefreshCw className={cn('h-4 w-4', syncAll.isPending && 'animate-spin')} aria-hidden /> Sync all
                        </Button>
                        <Button onClick={() => setImporting(!importing)} aria-expanded={importing}>
                            <Download className="h-4 w-4" aria-hidden /> {importing ? 'Close import' : 'Import'}
                        </Button>
                    </>
                ) : undefined}
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to see its requirements.</EmptyState>
            ) : projects.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : !project ? (
                <EmptyState title="Pick a project">The selected project isn't available. Choose another in the sidebar.</EmptyState>
            ) : (
                <>
                    {importing && <ImportPanel projectId={projectId} workspaceId={project.workspace_id} />}

                    {totals && (
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            {(Object.keys(COVERAGE_LABEL) as CoverageStatus[]).map((s) => (
                                <Tile key={s} label={COVERAGE_LABEL[s]} value={totals[s]} tone={COVERAGE_TONE[s]} />
                            ))}
                        </div>
                    )}

                    {list.isLoading ? (
                        <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
                    ) : list.error ? (
                        <EmptyState title="Couldn't load requirements">{errDetail(list.error)}</EmptyState>
                    ) : !reqs.length ? (
                        <EmptyState title="No requirements imported yet">Use Import to bring in tickets from a Jira or Linear connection.</EmptyState>
                    ) : (
                        <SplitView
                            list={GROUP_ORDER
                                .map((s) => ({ s, items: reqs.filter((r) => (r.coverage.status ?? 'not_covered') === s) }))
                                .filter((g) => g.items.length > 0)
                                .map((g) => (
                                    <ListGroup key={g.s} title={COVERAGE_LABEL[g.s]} count={g.items.length}>
                                        {g.items.map((r) => (
                                            <ListRow
                                                key={r.id}
                                                selected={current?.id === r.id}
                                                onSelect={() => setSelectedId(r.id)}
                                                title={<span className="break-words">{r.title}</span>}
                                                meta={
                                                    <span>
                                                        <span className="font-mono">{r.external_key}</span> · {r.criteria_count} criteria · {r.coverage.cases.length} linked test(s)
                                                        {r.ambiguous_count > 0 && <span className={toneClasses.butter.ink}> · {r.ambiguous_count} ambiguous</span>}
                                                        {r.sync_error && <span className={toneClasses.peach.ink}> · sync error</span>}
                                                    </span>
                                                }
                                                trailing={<CoveragePill status={r.coverage.status} />}
                                            />
                                        ))}
                                    </ListGroup>
                                ))}
                            detail={current && <RequirementDetail key={current.id} r={current} projectId={projectId} />}
                        />
                    )}
                </>
            )}
        </div>
    );
}
