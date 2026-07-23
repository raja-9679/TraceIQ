import { useState, useEffect, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    ShieldAlert, RefreshCw, AlertCircle, Layers, Save, Play, X, Plus, ShieldCheck, Lock,
    ChevronRight, ChevronDown,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { securityApi, SecuritySettings, SecurityScan, SecurityFinding } from '@/api/security';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const SEV_TONE: Record<string, string> = {
    high: 'bg-rose-50 text-rose-700 border-rose-200',
    medium: 'bg-amber-50 text-amber-700 border-amber-200',
    low: 'bg-sky-50 text-sky-700 border-sky-200',
    info: 'bg-slate-50 text-slate-600 border-slate-200',
};
const STATUS_TONE: Record<string, string> = {
    completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    running: 'bg-sky-50 text-sky-700 border-sky-200',
    pending: 'bg-slate-50 text-slate-600 border-slate-200',
    error: 'bg-rose-50 text-rose-700 border-rose-200',
};

function SettingsCard({ projectId, workspaceId }: { projectId: number; workspaceId: number | null }) {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['sec-settings', projectId], queryFn: () => securityApi.getSettings(projectId) });
    const [s, setS] = useState<SecuritySettings | null>(null);
    const [domainInput, setDomainInput] = useState('');
    useEffect(() => { if (data) setS(data); }, [data]);

    const wsSec = useQuery({
        queryKey: ['ws-security', workspaceId],
        queryFn: () => securityApi.getWorkspaceSecurity(workspaceId!),
        enabled: !!workspaceId,
    });
    const toggleWs = useMutation({
        mutationFn: (enabled: boolean) => securityApi.setWorkspaceActiveScan(workspaceId!, enabled),
        onSuccess: (d) => {
            toast.success(d.workspace_toggle ? 'Active scanning enabled for this workspace' : 'Active scanning disabled for this workspace');
            qc.invalidateQueries({ queryKey: ['ws-security', workspaceId] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    const save = useMutation({
        mutationFn: () => securityApi.setSettings(projectId, s!),
        onSuccess: () => { toast.success('Security settings saved'); qc.invalidateQueries({ queryKey: ['sec-settings', projectId] }); },
        onError: (e) => toast.error(`Save failed: ${errDetail(e)} (admin role required)`),
    });
    if (!s) return null;

    const ws = wsSec.data;

    const addDomain = () => {
        const d = domainInput.trim().toLowerCase();
        if (d && !s.allowed_domains.includes(d)) setS({ ...s, allowed_domains: [...s.allowed_domains, d] });
        setDomainInput('');
    };

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <h3 className="font-bold text-slate-800 mb-1 flex items-center gap-2"><Lock className="w-4 h-4 text-indigo-500" /> Authorization</h3>
            <p className="text-xs text-slate-400 mb-4">Scans are refused unless enabled and the target host is on the allowlist.</p>
            <label className="flex items-center justify-between text-sm cursor-pointer mb-3">
                <span className="text-slate-600">Security scanning enabled</span>
                <input type="checkbox" className="w-4 h-4" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />
            </label>
            <label className="flex items-center justify-between text-sm cursor-pointer mb-4">
                <span className="text-slate-600">Allow active (attacking) scans <span className="text-slate-400">(this project)</span></span>
                <input type="checkbox" className="w-4 h-4" checked={s.allow_active_scan} onChange={(e) => setS({ ...s, allow_active_scan: e.target.checked })} />
            </label>

            {ws && (
                <div className="mb-4 p-3 rounded-xl border border-amber-100 bg-amber-50/60">
                    <label className="flex items-center justify-between text-sm cursor-pointer">
                        <span className="text-slate-700 font-medium">Active scanning enabled for workspace</span>
                        <input type="checkbox" className="w-4 h-4"
                            checked={ws.active_scan_enabled}
                            disabled={!ws.can_edit || ws.forced_by_deployment || toggleWs.isPending}
                            onChange={(e) => toggleWs.mutate(e.target.checked)} />
                    </label>
                    <p className="text-[11px] text-slate-500 mt-1.5 leading-relaxed">
                        {ws.forced_by_deployment
                            ? 'Forced on by the deployment (SECURITY_ACTIVE_SCAN_ENABLED).'
                            : ws.can_edit
                                ? 'Workspace-wide master switch for attacking scans. Both this and the per-project toggle must be on. Only scan targets you own.'
                                : 'Only a workspace admin can change this. Active scans need this on plus the per-project toggle.'}
                    </p>
                </div>
            )}

            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Authorized domains</div>
            <div className="flex flex-wrap gap-1.5 mb-2">
                {s.allowed_domains.length === 0 && <span className="text-xs text-slate-400">none — scans will be refused</span>}
                {s.allowed_domains.map((d) => (
                    <Badge key={d} variant="outline" className="rounded-md bg-slate-50 text-slate-600 border-slate-200">
                        {d}
                        <button className="ml-1.5 text-slate-400 hover:text-rose-500" onClick={() => setS({ ...s, allowed_domains: s.allowed_domains.filter((x) => x !== d) })}><X className="w-3 h-3" /></button>
                    </Badge>
                ))}
            </div>
            <div className="flex gap-2 mb-4">
                <Input placeholder="example.com" value={domainInput} className="h-8"
                    onChange={(e) => setDomainInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addDomain()} />
                <Button size="sm" variant="outline" className="h-8" onClick={addDomain}><Plus className="w-3.5 h-3.5" /></Button>
            </div>
            <Button size="sm" className="h-9 rounded-lg" onClick={() => save.mutate()} disabled={save.isPending}>
                <Save className="w-3.5 h-3.5 mr-1.5" /> Save
            </Button>
        </div>
    );
}

const AUTH_PRESETS: Record<string, { name: string; placeholder: string; wrap: (v: string) => string }> = {
    bearer: { name: 'Authorization', placeholder: 'JWT / access token', wrap: (v) => `Bearer ${v}` },
    apikey: { name: 'X-API-Key', placeholder: 'API key', wrap: (v) => v },
    custom: { name: '', placeholder: 'header value', wrap: (v) => v },
};

function NewScanCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [target, setTarget] = useState('');
    const [scanType, setScanType] = useState('baseline');
    const [authenticated, setAuthenticated] = useState(false);
    const [authorized, setAuthorized] = useState(false);
    // Advanced: API import + header auth (items 6 & 7).
    const [advanced, setAdvanced] = useState(false);
    const [openapiUrl, setOpenapiUrl] = useState('');
    const [authMode, setAuthMode] = useState<'none' | 'bearer' | 'apikey' | 'custom'>('none');
    const [tokenValue, setTokenValue] = useState('');
    const [customHeader, setCustomHeader] = useState('');

    const preset = authMode === 'none' ? null : AUTH_PRESETS[authMode];
    const headerName = authMode === 'custom' ? customHeader.trim() : preset?.name;
    const headerValue = preset && tokenValue.trim() ? preset.wrap(tokenValue.trim()) : null;
    const authIncomplete = authMode !== 'none' && (!tokenValue.trim() || (authMode === 'custom' && !customHeader.trim()));

    const reset = () => {
        setTarget(''); setAuthorized(false); setOpenapiUrl('');
        setAuthMode('none'); setTokenValue(''); setCustomHeader(''); setAdvanced(false);
    };
    const run = useMutation({
        mutationFn: () => securityApi.createScan(projectId, {
            target_url: target, scan_type: scanType, authenticated, authorized,
            openapi_url: openapiUrl.trim() || null,
            auth_header_name: headerValue ? headerName : null,
            auth_header_value: headerValue,
        }),
        onSuccess: () => { toast.success('Scan queued'); reset(); qc.invalidateQueries({ queryKey: ['sec-scans', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2"><ShieldCheck className="w-4 h-4 text-emerald-500" /> New scan</h3>
            <div className="space-y-3">
                <div>
                    <label className="text-[11px] font-semibold uppercase text-slate-400 mb-1 block">Target URL</label>
                    <Input placeholder="https://staging.example.com" value={target} onChange={(e) => setTarget(e.target.value)} className="h-9" />
                </div>
                <div className="flex gap-3">
                    <Select value={scanType} onValueChange={setScanType}>
                        <SelectTrigger className="h-9 flex-1"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="baseline">Baseline (passive)</SelectItem>
                            <SelectItem value="active">Active (attacking)</SelectItem>
                        </SelectContent>
                    </Select>
                    <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer whitespace-nowrap">
                        <input type="checkbox" className="w-4 h-4" checked={authenticated} onChange={(e) => setAuthenticated(e.target.checked)} /> Use saved session
                    </label>
                </div>

                {/* Advanced: API import + header auth */}
                <button
                    type="button"
                    onClick={() => setAdvanced((a) => !a)}
                    className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-700"
                >
                    {advanced ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                    API import &amp; token auth
                    {(openapiUrl || authMode !== 'none') && <Badge variant="outline" className="ml-1 text-[9px] bg-sky-50 text-sky-600 border-sky-200 rounded">configured</Badge>}
                </button>

                {advanced && (
                    <div className="space-y-3 rounded-xl border border-slate-100 bg-slate-50/60 p-3">
                        <div>
                            <label className="text-[11px] font-semibold uppercase text-slate-400 mb-1 flex items-center gap-1">
                                <Layers className="w-3 h-3" /> OpenAPI / Swagger spec URL
                            </label>
                            <Input placeholder="https://api.example.com/openapi.json" value={openapiUrl}
                                onChange={(e) => setOpenapiUrl(e.target.value)} className="h-8 text-xs" />
                            <p className="text-[10px] text-slate-400 mt-1">Imports every documented endpoint so the scan reaches routes the crawler can't find by following links.</p>
                        </div>
                        <div>
                            <label className="text-[11px] font-semibold uppercase text-slate-400 mb-1 flex items-center gap-1">
                                <Lock className="w-3 h-3" /> Token / header auth
                            </label>
                            <div className="flex gap-2">
                                <Select value={authMode} onValueChange={(v) => setAuthMode(v as typeof authMode)}>
                                    <SelectTrigger className="h-8 w-32 text-xs shrink-0"><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="none">None</SelectItem>
                                        <SelectItem value="bearer">Bearer</SelectItem>
                                        <SelectItem value="apikey">API key</SelectItem>
                                        <SelectItem value="custom">Custom header</SelectItem>
                                    </SelectContent>
                                </Select>
                                {authMode === 'custom' && (
                                    <Input placeholder="Header-Name" value={customHeader}
                                        onChange={(e) => setCustomHeader(e.target.value)} className="h-8 text-xs w-32 shrink-0" />
                                )}
                                {authMode !== 'none' && (
                                    <Input type="password" autoComplete="off" placeholder={preset?.placeholder}
                                        value={tokenValue} onChange={(e) => setTokenValue(e.target.value)} className="h-8 text-xs flex-1" />
                                )}
                            </div>
                            {headerValue && (
                                <p className="text-[10px] text-slate-400 mt-1 font-mono truncate">
                                    Sends: {headerName}: {authMode === 'bearer' ? 'Bearer ••••••' : '••••••'}
                                </p>
                            )}
                            <p className="text-[10px] text-slate-400 mt-1">Injected on every request. Stored only for this run, then discarded.</p>
                        </div>
                    </div>
                )}

                <label className="flex items-start gap-2 text-xs text-slate-600 cursor-pointer bg-amber-50/60 border border-amber-100 rounded-lg px-3 py-2">
                    <input type="checkbox" className="w-4 h-4 mt-px" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
                    I am authorized to scan this target (it belongs to me/my organization).
                </label>
                <Button size="sm" className="h-9 rounded-lg w-full" onClick={() => run.mutate()} disabled={run.isPending || !target || !authorized || authIncomplete}>
                    <Play className="w-3.5 h-3.5 mr-1.5" /> Run scan
                </Button>
            </div>
        </div>
    );
}

const FINDING_STATUSES = ['open', 'acknowledged', 'false_positive', 'resolved'] as const;
const FINDING_STATUS_TONE: Record<string, string> = {
    open: 'text-rose-600', acknowledged: 'text-amber-600',
    false_positive: 'text-slate-400', resolved: 'text-emerald-600',
};

// Minimal inline markdown → JSX for finding bodies (**bold**, `code`, links).
function inlineMd(text: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    const re = /\*\*(.+?)\*\*|`([^`]+?)`|(https?:\/\/[^\s]+)/g;
    let last = 0, m: RegExpExecArray | null, i = 0;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) nodes.push(text.slice(last, m.index));
        if (m[1]) nodes.push(<strong key={i++} className="font-semibold text-slate-700">{m[1]}</strong>);
        else if (m[2]) nodes.push(<code key={i++} className="px-1 py-0.5 bg-slate-100 rounded text-[11px] font-mono text-slate-700">{m[2]}</code>);
        else if (m[3]) nodes.push(<a key={i++} href={m[3]} target="_blank" rel="noreferrer" className="text-sky-600 hover:underline break-all">{m[3]}</a>);
        last = m.index + m[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes;
}

function FindingBody({ text }: { text: string }) {
    return (
        <div className="space-y-1.5">
            {text.split('\n').map((line, i) => {
                const t = line.trim();
                if (!t) return null;
                if (t.startsWith('- ')) return <div key={i} className="flex gap-1.5 pl-1"><span className="text-slate-400">•</span><span className="min-w-0 break-words">{inlineMd(t.slice(2))}</span></div>;
                return <p key={i} className="break-words">{inlineMd(t)}</p>;
            })}
        </div>
    );
}

function FindingRow({ f, onTriage }: { f: SecurityFinding; onTriage: (status: string) => void }) {
    const [open, setOpen] = useState(false);
    const muted = f.status === 'false_positive' || f.status === 'resolved';
    const hasDetail = !!(f.description || f.evidence);
    return (
        <div className="border border-slate-100 rounded-md">
            <div className="flex items-start gap-2 text-sm p-2">
                <button
                    className="mt-0.5 text-slate-400 hover:text-slate-600 shrink-0 disabled:opacity-30"
                    disabled={!hasDetail}
                    onClick={() => setOpen((o) => !o)}
                    aria-label={open ? 'Collapse finding' : 'Expand finding'}
                >
                    {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                </button>
                <Badge variant="outline" className={`rounded-md text-[9px] font-bold uppercase shrink-0 ${SEV_TONE[f.severity]}`}>{f.severity}</Badge>
                <div className="min-w-0 flex-1 cursor-pointer" onClick={() => hasDetail && setOpen((o) => !o)}>
                    <span className={`font-medium ${muted ? 'text-slate-400 line-through' : 'text-slate-700'}`}>{f.title}</span>
                    {f.target_url && <span className="text-xs text-slate-400 ml-1 break-all">{f.target_url}</span>}
                </div>
                <select
                    className={`text-[11px] font-semibold bg-transparent border border-slate-200 rounded-md px-1.5 py-0.5 shrink-0 ${FINDING_STATUS_TONE[f.status || 'open']}`}
                    value={f.status || 'open'}
                    onChange={(e) => onTriage(e.target.value)}
                >
                    {FINDING_STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                </select>
            </div>
            {open && hasDetail && (
                <div className="px-3 pb-3 pl-9 text-xs text-slate-600 space-y-3">
                    {f.description && <FindingBody text={f.description} />}
                    {f.evidence && (
                        <div>
                            <div className="text-[10px] font-bold uppercase text-slate-400 mb-1">Evidence</div>
                            <pre className="bg-slate-50 border border-slate-100 rounded p-2 overflow-x-auto text-[11px] font-mono text-slate-700 whitespace-pre-wrap break-all">{f.evidence}</pre>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function ScanRow({ scan }: { scan: SecurityScan }) {
    const [open, setOpen] = useState(false);
    const qc = useQueryClient();
    const { data: detail } = useQuery({
        queryKey: ['sec-scan', scan.id],
        queryFn: () => securityApi.getScan(scan.id),
        enabled: open,
    });
    const { data: diff } = useQuery({
        queryKey: ['sec-scan-diff', scan.id],
        queryFn: () => securityApi.scanDiff(scan.id),
        enabled: open && scan.status === 'completed',
    });
    const triage = useMutation({
        mutationFn: ({ id, status }: { id: number; status: string }) => securityApi.updateFinding(id, { status }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['sec-scan', scan.id] }),
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Update failed'),
    });
    const counts = scan.counts || {};
    return (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
            <button className="w-full flex items-center justify-between p-4 hover:bg-slate-50/50 text-left" onClick={() => setOpen(!open)}>
                <div className="min-w-0">
                    <div className="font-semibold text-slate-800 text-sm truncate">{scan.target_url}</div>
                    <div className="text-xs text-slate-400 mt-0.5 flex items-center gap-1.5 flex-wrap">
                        <span>#{scan.id} · {scan.scan_type}{scan.authenticated && ' · session'} · {new Date(scan.created_at).toLocaleString()}</span>
                        {scan.openapi_url && <Badge variant="outline" className="text-[9px] bg-sky-50 text-sky-600 border-sky-200 rounded">API import</Badge>}
                        {scan.auth_header_name && <Badge variant="outline" className="text-[9px] bg-violet-50 text-violet-600 border-violet-200 rounded">{scan.auth_header_name}</Badge>}
                    </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                    {(['high', 'medium', 'low', 'info'] as const).map((s) => counts[s] ? (
                        <Badge key={s} variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${SEV_TONE[s]}`}>{counts[s]} {s[0]}</Badge>
                    ) : null)}
                    <Badge variant="outline" className={`rounded-md text-[10px] font-bold uppercase ${STATUS_TONE[scan.status] || ''}`}>{scan.status}</Badge>
                </div>
            </button>
            {open && (
                <div className="border-t border-slate-100 p-4 bg-slate-50/30">
                    {scan.error && <p className="text-sm text-rose-600 mb-2">{scan.error}</p>}
                    {diff?.baseline_available && (
                        <p className="text-xs mb-3 text-slate-500">
                            vs scan #{diff.previous_scan_id}:{' '}
                            <span className={diff.new.length ? 'text-rose-600 font-semibold' : ''}>{diff.new.length} new</span>
                            {' · '}
                            <span className={diff.fixed.length ? 'text-emerald-600 font-semibold' : ''}>{diff.fixed.length} fixed</span>
                            {' · '}{diff.persisting_count} persisting
                        </p>
                    )}
                    {!detail ? <RefreshCw className="animate-spin w-4 h-4 text-slate-400" />
                        : detail.findings.length === 0 ? <p className="text-sm text-slate-400">No findings.</p>
                            : (
                                <div className="space-y-2">
                                    {detail.findings.map((f) => (
                                        <FindingRow key={f.id} f={f} onTriage={(status) => triage.mutate({ id: f.id, status })} />
                                    ))}
                                </div>
                            )}
                </div>
            )}
        </div>
    );
}

export default function Security() {
    const [projectId, setProjectId] = useState<number | null>(() => {
        const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null;
    });
    useEffect(() => {
        const h = () => { const s = localStorage.getItem('activeProjectId'); setProjectId(s ? parseInt(s) : null); };
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const selectProject = (idStr: string) => {
        const id = parseInt(idStr); setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };
    const scans = useQuery({
        queryKey: ['sec-scans', projectId],
        queryFn: () => securityApi.listScans(projectId!),
        enabled: !!projectId,
        refetchInterval: 15000,
    });

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Security</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Security Scans</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Authenticated DAST (OWASP ZAP) against authorized targets. Baseline is passive and safe;
                            active scanning attacks the target and needs explicit opt-in.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
                        <Select value={projectId?.toString() ?? ''} onValueChange={selectProject}>
                            <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                                <div className="flex items-center gap-2 min-w-0">
                                    <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                                    <SelectValue placeholder="Select a project" />
                                </div>
                            </SelectTrigger>
                            <SelectContent>
                                {(projects || []).map((p) => <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!projectId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
                    <p className="text-slate-500 text-sm">Select a project above to manage scans.</p>
                </div>
            ) : (
                <div className="space-y-6">
                    <div className="grid md:grid-cols-2 gap-6">
                        <SettingsCard projectId={projectId} workspaceId={(projects || []).find((p) => p.id === projectId)?.workspace_id ?? null} />
                        <NewScanCard projectId={projectId} />
                    </div>
                    <div>
                        <h3 className="font-bold text-slate-800 mb-3 flex items-center gap-2"><ShieldAlert className="w-4 h-4 text-slate-500" /> Scans</h3>
                        {scans.isLoading ? <div className="p-8 flex justify-center"><RefreshCw className="animate-spin w-5 h-5 text-slate-400" /></div>
                            : scans.error ? <p className="text-sm text-rose-600">{errDetail(scans.error)}</p>
                                : !scans.data || scans.data.length === 0 ? (
                                    <div className="p-10 text-center bg-white border border-slate-200 rounded-2xl">
                                        <ShieldAlert className="w-9 h-9 text-slate-300 mx-auto mb-3" />
                                        <p className="text-slate-500 text-sm">No scans yet. Configure authorization, then run one above.</p>
                                    </div>
                                ) : (
                                    <div className="space-y-2">
                                        {scans.data.map((s) => <ScanRow key={s.id} scan={s} />)}
                                    </div>
                                )}
                    </div>
                </div>
            )}
        </div>
    );
}
