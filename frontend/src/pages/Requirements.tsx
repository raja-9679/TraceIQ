import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    ClipboardCheck, RefreshCw, AlertCircle, Layers, ExternalLink, Download, Search, Sparkles, Trash2,
    AlertTriangle, CheckCircle2, XCircle, MinusCircle, CircleDashed,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects, getTestSuites } from '@/lib/api';
import { ticketsApi } from '@/api/tickets';
import {
    requirementsApi, COVERAGE_LABEL, CoverageStatus, ImportedRequirement, ExternalIssueSummary,
} from '@/api/requirements';

const errDetail = (e: unknown): string => {
    const err = e as { response?: { data?: { detail?: string } }; message?: string } | null;
    return err?.response?.data?.detail || err?.message || 'Unknown error';
};
type SuiteOption = { id: number; name: string };

const COVERAGE_STYLE: Record<CoverageStatus, { tone: string; icon: LucideIcon }> = {
    covered: { tone: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: CheckCircle2 },
    failing: { tone: 'bg-rose-50 text-rose-700 border-rose-200', icon: XCircle },
    untested: { tone: 'bg-amber-50 text-amber-700 border-amber-200', icon: MinusCircle },
    not_covered: { tone: 'bg-slate-50 text-slate-500 border-slate-200', icon: CircleDashed },
};

function CoverageBadge({ status }: { status: CoverageStatus }) {
    const S = COVERAGE_STYLE[status] || COVERAGE_STYLE.not_covered;
    return (
        <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase shrink-0 ${S.tone}`}>
            <S.icon className="w-3 h-3 mr-1" /> {COVERAGE_LABEL[status]}
        </Badge>
    );
}

function ImportPanel({ projectId, workspaceId }: { projectId: number; workspaceId: number }) {
    const qc = useQueryClient();
    const trackers = useQuery({ queryKey: ['trackers', workspaceId], queryFn: () => ticketsApi.listConfigs(workspaceId) });
    const importable = (trackers.data || []).filter((c) => c.enabled && (c.provider === 'jira' || c.provider === 'linear'));
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
            <div className="bg-white border border-slate-200 rounded-2xl p-5 text-sm text-slate-500">
                No Jira or Linear connection in this workspace yet. Add one on the <Link to="/issue-trackers" className="text-indigo-600 hover:underline">Issue Trackers</Link> page.
            </div>
        );
    }
    const toggle = (k: string) => setPicked((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
    const keyList = keys.split(/[\s,]+/).map((k) => k.trim()).filter(Boolean);

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-4">
            <div className="flex flex-wrap items-end gap-3">
                <div>
                    <label htmlFor="req-tracker" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Connection</label>
                    <Select value={config.id.toString()} onValueChange={(v) => { setConfigId(parseInt(v)); setResults([]); }}>
                        <SelectTrigger id="req-tracker" className="h-9 mt-1 w-[220px]"><SelectValue /></SelectTrigger>
                        <SelectContent>{importable.map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name} ({c.provider})</SelectItem>)}</SelectContent>
                    </Select>
                </div>
                {config.provider === 'jira' ? (
                    <div className="flex-1 min-w-[260px]">
                        <label htmlFor="req-jql" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">JQL</label>
                        <Input id="req-jql" className="h-9 mt-1 font-mono text-xs" placeholder='project = SHOP AND sprint in openSprints()' value={jql} onChange={(e) => setJql(e.target.value)} />
                    </div>
                ) : (
                    <>
                        <div>
                            <label htmlFor="req-team" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Team key</label>
                            <Input id="req-team" className="h-9 mt-1 w-28" placeholder="ENG" value={teamKey} onChange={(e) => setTeamKey(e.target.value)} />
                        </div>
                        <div>
                            <label htmlFor="req-query" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Title contains</label>
                            <Input id="req-query" className="h-9 mt-1 w-48" value={query} onChange={(e) => setQuery(e.target.value)} />
                        </div>
                        <div>
                            <label htmlFor="req-label" className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Label</label>
                            <Input id="req-label" className="h-9 mt-1 w-36" value={label} onChange={(e) => setLabel(e.target.value)} />
                        </div>
                    </>
                )}
                <Button size="sm" className="h-9 rounded-lg" onClick={() => search.mutate()} disabled={search.isPending || (config.provider === 'jira' && !jql.trim())}>
                    <Search className="w-4 h-4 mr-1" /> Search
                </Button>
            </div>

            {results.length > 0 && (
                <div className="border border-slate-100 rounded-xl divide-y divide-slate-100 max-h-[320px] overflow-y-auto">
                    {results.map((i) => (
                        <label key={i.key} className="flex items-center gap-3 px-3 py-2 text-sm cursor-pointer hover:bg-slate-50">
                            <Checkbox checked={picked.has(i.key)} onCheckedChange={() => toggle(i.key)} aria-label={`Select ${i.key}`} />
                            <span className="font-mono text-xs text-slate-500 w-24 shrink-0">{i.key}</span>
                            <span className="flex-1 truncate text-slate-700">{i.title}</span>
                            {i.status && <span className="text-xs text-slate-400">{i.status}</span>}
                            {i.already_imported && <Badge variant="outline" className="text-[10px]">imported</Badge>}
                        </label>
                    ))}
                </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
                {results.length > 0 && (
                    <Button size="sm" variant="outline" className="h-9 rounded-lg" disabled={!picked.size || doImport.isPending} onClick={() => doImport.mutate([...picked])}>
                        <Download className="w-4 h-4 mr-1" /> Import {picked.size || ''} selected
                    </Button>
                )}
                <label htmlFor="req-keys" className="sr-only">Issue keys</label>
                <Input id="req-keys" className="h-9 w-64" placeholder="…or keys: SHOP-12, SHOP-14" value={keys} onChange={(e) => setKeys(e.target.value)} />
                <Button size="sm" variant="outline" className="h-9 rounded-lg" disabled={!keyList.length || doImport.isPending} onClick={() => doImport.mutate(keyList)}>
                    Import by key
                </Button>
            </div>
        </div>
    );
}

function RequirementRow({ r, projectId }: { r: ImportedRequirement; projectId: number }) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const detail = useQuery({ queryKey: ['requirement', r.id], queryFn: () => requirementsApi.get(r.id), enabled: open });
    const suites = useQuery({ queryKey: ['suites', projectId], queryFn: () => getTestSuites(projectId), enabled: open });
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
        <div className="border border-slate-200 rounded-xl overflow-hidden bg-white">
            <button className="w-full flex items-center gap-3 p-4 hover:bg-slate-50/50 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
                <div className="min-w-0 flex-1">
                    <div className="font-semibold text-slate-800 text-sm flex items-center gap-2 min-w-0">
                        <span className="font-mono text-xs text-indigo-600 shrink-0">{r.external_key}</span>
                        <span className="truncate">{r.title}</span>
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                        {r.criteria_count} criteria · {r.proposed_count} proposed
                        {r.ambiguous_count > 0 && <span className="text-amber-600"> · {r.ambiguous_count} ambiguous</span>}
                        {' '}· {r.coverage.cases.length} linked test(s) · synced {new Date(r.last_synced_at + 'Z').toLocaleString()}
                        {r.sync_error && <span className="text-rose-600"> · sync error</span>}
                    </div>
                </div>
                <CoverageBadge status={r.coverage.status} />
            </button>
            {open && (
                <div className="border-t border-slate-100 p-4 bg-slate-50/30 space-y-4">
                    <div className="flex flex-wrap gap-2">
                        {r.url && <a href={r.url} target="_blank" rel="noreferrer" className="text-xs text-indigo-600 hover:underline inline-flex items-center gap-1">Open in {r.source} <ExternalLink className="w-3 h-3" /></a>}
                        <span className="flex-1" />
                        <Button size="sm" variant="outline" className="h-8 text-xs" onClick={() => sync.mutate()} disabled={sync.isPending}><RefreshCw className="w-3.5 h-3.5 mr-1" /> Re-sync</Button>
                        <Button size="sm" variant="outline" className="h-8 text-xs text-rose-600" onClick={() => { if (confirm(`Remove ${r.external_key}? Linked tests are kept.`)) remove.mutate(); }} disabled={remove.isPending}><Trash2 className="w-3.5 h-3.5 mr-1" /> Remove</Button>
                    </div>
                    {r.sync_error && <p className="text-xs text-rose-600">{r.sync_error}</p>}

                    <div>
                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Acceptance criteria</div>
                        {detail.isLoading ? <RefreshCw className="animate-spin w-4 h-4 text-slate-400" /> : (
                            <ol className="space-y-2">
                                {(detail.data?.acceptance_criteria || []).map((c) => (
                                    <li key={c.index} className="bg-white border border-slate-200 rounded-lg p-3 text-sm">
                                        <div className="flex items-start gap-2">
                                            <span className="text-xs font-mono text-slate-400 shrink-0 mt-0.5">AC{c.index + 1}</span>
                                            <pre className="whitespace-pre-wrap font-sans text-slate-700 flex-1">{c.text}</pre>
                                            {c.proposal_id ? (
                                                <Link to="/proposals" className="shrink-0">
                                                    <Badge variant="outline" className="text-[10px]">#{c.proposal_id} {c.proposal_status}{c.needs_authoring ? ' · needs authoring' : ''}</Badge>
                                                </Link>
                                            ) : null}
                                        </div>
                                        {c.ambiguous && (
                                            <div className="mt-1.5 text-xs text-amber-700 flex items-center gap-1">
                                                <AlertTriangle className="w-3.5 h-3.5" /> Ambiguous: {c.reasons.join('; ')}
                                            </div>
                                        )}
                                    </li>
                                ))}
                            </ol>
                        )}
                    </div>

                    <div>
                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Covered by</div>
                        {r.coverage.cases.length === 0 ? <p className="text-xs text-slate-400">No linked tests. Accepting a generated proposal links it automatically.</p> : (
                            <div className="flex flex-wrap gap-1.5">
                                {r.coverage.cases.map((c) => (
                                    <Badge key={c.id} variant="outline" className="rounded-md bg-white text-slate-600 border-slate-200">
                                        {c.name} · {c.latest_status ? c.latest_status.toLowerCase() : 'not run'}
                                    </Badge>
                                ))}
                            </div>
                        )}
                    </div>

                    <div className="bg-white border border-slate-200 rounded-lg p-3 flex flex-wrap items-end gap-3">
                        <div>
                            <label htmlFor={`gen-suite-${r.id}`} className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Target suite</label>
                            <Select value={suiteId} onValueChange={setSuiteId}>
                                <SelectTrigger id={`gen-suite-${r.id}`} className="h-9 mt-1 w-[200px]"><SelectValue placeholder="Choose a suite" /></SelectTrigger>
                                <SelectContent>{((suites.data || []) as SuiteOption[]).map((s) => <SelectItem key={s.id} value={s.id.toString()}>{s.name}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                        <div className="flex-1 min-w-[200px]">
                            <label htmlFor={`gen-url-${r.id}`} className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Target URL (optional)</label>
                            <Input id={`gen-url-${r.id}`} className="h-9 mt-1" placeholder="https://staging.example.com/checkout" value={targetUrl} onChange={(e) => setTargetUrl(e.target.value)} />
                        </div>
                        <label className="flex items-center gap-2 text-xs text-slate-600 h-9">
                            <Checkbox checked={includeAmbiguous} onCheckedChange={(v) => setIncludeAmbiguous(!!v)} /> Include ambiguous
                        </label>
                        <Button size="sm" className="h-9 rounded-lg" disabled={!suiteId || generate.isPending} onClick={() => generate.mutate()}>
                            <Sparkles className="w-4 h-4 mr-1" /> {generate.isPending ? 'Generating…' : 'Generate tests'}
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}

export default function Requirements() {
    const qc = useQueryClient();
    const [projectId, setProjectId] = useState<number | null>(() => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const project = useMemo(() => (projects || []).find((p) => p.id === projectId), [projects, projectId]);
    const selectProject = (idStr: string) => { const id = parseInt(idStr); setProjectId(id); localStorage.setItem('activeProjectId', id.toString()); window.dispatchEvent(new Event('projectChanged')); };
    const [importing, setImporting] = useState(false);

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

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Requirements</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Requirements</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Import tickets from Jira or Linear, turn each acceptance criterion into a test proposal, and see which
                            requirements are covered, failing or untested. Vague criteria are flagged instead of guessed at.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200" aria-label="Project"><div className="flex items-center gap-2 min-w-0"><Layers className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Select a project" /></div></SelectTrigger>
                            <SelectContent>{(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!projectId || !project ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">Select a project.</p></div>
            ) : (
                <div className="space-y-6">
                    {totals && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                            {(Object.keys(COVERAGE_LABEL) as CoverageStatus[]).map((s) => (
                                <div key={s} className="bg-white border border-slate-200 rounded-2xl p-4">
                                    <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{COVERAGE_LABEL[s]}</div>
                                    <div className="text-2xl font-extrabold text-slate-800 tabular-nums mt-1">{totals[s]}</div>
                                </div>
                            ))}
                        </div>
                    )}

                    <div className="flex items-center gap-2">
                        <h3 className="font-bold text-slate-800 flex items-center gap-2"><ClipboardCheck className="w-4 h-4 text-slate-500" /> Imported requirements</h3>
                        <span className="flex-1" />
                        <Button size="sm" variant="outline" className="h-9 rounded-lg" onClick={() => syncAll.mutate()} disabled={syncAll.isPending || !list.data?.requirements.length}>
                            <RefreshCw className={`w-4 h-4 mr-1 ${syncAll.isPending ? 'animate-spin' : ''}`} /> Sync all
                        </Button>
                        <Button size="sm" className="h-9 rounded-lg" onClick={() => setImporting(!importing)}>
                            <Download className="w-4 h-4 mr-1" /> {importing ? 'Close import' : 'Import'}
                        </Button>
                    </div>
                    {importing && <ImportPanel projectId={projectId} workspaceId={project.workspace_id} />}

                    {list.isLoading ? <div className="p-8 flex justify-center"><RefreshCw className="animate-spin w-5 h-5 text-slate-400" /></div>
                        : list.error ? <p className="text-sm text-rose-600">{errDetail(list.error)}</p>
                            : !list.data?.requirements.length ? (
                                <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
                                    <ClipboardCheck className="w-9 h-9 text-slate-300 mx-auto mb-3" />
                                    <p className="text-slate-500 text-sm">No requirements imported yet.</p>
                                </div>
                            ) : (
                                <div className="space-y-2">{list.data.requirements.map((r) => <RequirementRow key={r.id} r={r} projectId={projectId} />)}</div>
                            )}
                </div>
            )}
        </div>
    );
}
