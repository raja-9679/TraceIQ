import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    ShieldAlert, RefreshCw, AlertCircle, Layers, Save, Play, X, Plus, ShieldCheck, Lock,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { securityApi, SecuritySettings, SecurityScan } from '@/api/security';

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

function SettingsCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const { data } = useQuery({ queryKey: ['sec-settings', projectId], queryFn: () => securityApi.getSettings(projectId) });
    const [s, setS] = useState<SecuritySettings | null>(null);
    const [domainInput, setDomainInput] = useState('');
    useEffect(() => { if (data) setS(data); }, [data]);

    const save = useMutation({
        mutationFn: () => securityApi.setSettings(projectId, s!),
        onSuccess: () => { toast.success('Security settings saved'); qc.invalidateQueries({ queryKey: ['sec-settings', projectId] }); },
        onError: (e) => toast.error(`Save failed: ${errDetail(e)} (admin role required)`),
    });
    if (!s) return null;

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
                <span className="text-slate-600">Allow active (attacking) scans</span>
                <input type="checkbox" className="w-4 h-4" checked={s.allow_active_scan} onChange={(e) => setS({ ...s, allow_active_scan: e.target.checked })} />
            </label>
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

function NewScanCard({ projectId }: { projectId: number }) {
    const qc = useQueryClient();
    const [target, setTarget] = useState('');
    const [scanType, setScanType] = useState('baseline');
    const [authenticated, setAuthenticated] = useState(false);
    const [authorized, setAuthorized] = useState(false);

    const run = useMutation({
        mutationFn: () => securityApi.createScan(projectId, { target_url: target, scan_type: scanType, authenticated, authorized }),
        onSuccess: () => { toast.success('Scan queued'); setTarget(''); setAuthorized(false); qc.invalidateQueries({ queryKey: ['sec-scans', projectId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <h3 className="font-bold text-slate-800 mb-4 flex items-center gap-2"><ShieldCheck className="w-4 h-4 text-emerald-500" /> New scan</h3>
            <div className="space-y-3">
                <Input placeholder="https://staging.example.com" value={target} onChange={(e) => setTarget(e.target.value)} className="h-9" />
                <div className="flex gap-3">
                    <Select value={scanType} onValueChange={setScanType}>
                        <SelectTrigger className="h-9 flex-1"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="baseline">Baseline (passive)</SelectItem>
                            <SelectItem value="active">Active (attacking)</SelectItem>
                        </SelectContent>
                    </Select>
                    <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
                        <input type="checkbox" className="w-4 h-4" checked={authenticated} onChange={(e) => setAuthenticated(e.target.checked)} /> Authenticated
                    </label>
                </div>
                <label className="flex items-start gap-2 text-xs text-slate-600 cursor-pointer bg-amber-50/60 border border-amber-100 rounded-lg px-3 py-2">
                    <input type="checkbox" className="w-4 h-4 mt-px" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
                    I am authorized to scan this target (it belongs to me/my organization).
                </label>
                <Button size="sm" className="h-9 rounded-lg w-full" onClick={() => run.mutate()} disabled={run.isPending || !target || !authorized}>
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
                    <div className="text-xs text-slate-400 mt-0.5">
                        #{scan.id} · {scan.scan_type}{scan.authenticated && ' · authenticated'} · {new Date(scan.created_at).toLocaleString()}
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
                                        <div key={f.id} className="flex items-start gap-2 text-sm">
                                            <Badge variant="outline" className={`rounded-md text-[9px] font-bold uppercase shrink-0 ${SEV_TONE[f.severity]}`}>{f.severity}</Badge>
                                            <div className="min-w-0 flex-1">
                                                <span className={`font-medium ${f.status === 'false_positive' || f.status === 'resolved' ? 'text-slate-400 line-through' : 'text-slate-700'}`}>{f.title}</span>
                                                {f.target_url && <span className="text-xs text-slate-400 ml-1">{f.target_url}</span>}
                                            </div>
                                            <select
                                                className={`text-[11px] font-semibold bg-transparent border border-slate-200 rounded-md px-1.5 py-0.5 shrink-0 ${FINDING_STATUS_TONE[f.status || 'open']}`}
                                                value={f.status || 'open'}
                                                onChange={(e) => triage.mutate({ id: f.id, status: e.target.value })}
                                            >
                                                {FINDING_STATUSES.map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
                                            </select>
                                        </div>
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
                        <SettingsCard projectId={projectId} />
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
