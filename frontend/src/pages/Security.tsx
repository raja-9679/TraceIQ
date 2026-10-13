import { useState, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { formatDistanceToNowStrict } from 'date-fns';
import { Play, X, Plus, ChevronRight, ChevronDown, Lock, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { useActiveProjectId } from '@/hooks/useActiveProject';
import { securityApi, type SecuritySettings, type SecurityScan, type SecurityFinding } from '@/api/security';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Panel, Pill, SplitView,
} from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { cn } from '@/lib/utils';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const ago = (iso: string) => formatDistanceToNowStrict(new Date(iso), { addSuffix: true });

const SEV_TONE: Record<string, Tone> = { high: 'peach', medium: 'butter', low: 'neutral', info: 'neutral' };
const SCAN_TONE: Record<string, Tone> = { completed: 'mint', running: 'lavender', pending: 'neutral', error: 'peach' };

const STATUSES = ['open', 'acknowledged', 'false_positive', 'resolved'] as const;
type FindingStatus = typeof STATUSES[number];
const STATUS_LABEL: Record<FindingStatus, string> = {
    open: 'Open', acknowledged: 'Acknowledged', false_positive: 'False positive', resolved: 'Resolved',
};

// --- settings + new scan (kept from the previous page, restyled) ---------------

function SettingsForm({ projectId, workspaceId, initial }: { projectId: number; workspaceId: number | null; initial: SecuritySettings }) {
    const qc = useQueryClient();
    const [s, setS] = useState<SecuritySettings>(initial);
    const [domainInput, setDomainInput] = useState('');

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
        mutationFn: () => securityApi.setSettings(projectId, s),
        onSuccess: () => { toast.success('Security settings saved'); qc.invalidateQueries({ queryKey: ['sec-settings', projectId] }); },
        onError: (e) => toast.error(`Save failed: ${errDetail(e)} (admin role required)`),
    });

    const ws = wsSec.data;
    const addDomain = () => {
        const d = domainInput.trim().toLowerCase();
        if (d && !s.allowed_domains.includes(d)) setS({ ...s, allowed_domains: [...s.allowed_domains, d] });
        setDomainInput('');
    };

    return (
        <section className="flex max-w-3xl flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <div>
                <h2 className="flex items-center gap-2 text-base font-semibold text-ink"><Lock className="h-4 w-4" /> Authorization</h2>
                <p className="mt-1 text-[13px] text-ink-soft">Scans are refused unless scanning is on and the target host is on the allowlist.</p>
            </div>
            <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-2xl border border-border px-4 text-sm">
                <span>Security scanning enabled</span>
                <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />
            </label>
            <label className="flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-2xl border border-border px-4 text-sm">
                <span>Allow active (attacking) scans <span className="text-ink-soft">for this project</span></span>
                <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={s.allow_active_scan} onChange={(e) => setS({ ...s, allow_active_scan: e.target.checked })} />
            </label>

            {ws && (
                <div className="flex flex-col gap-1.5 rounded-2xl border border-border p-4 shadow-[inset_4px_0_0_#F2C94C]">
                    <label className="flex cursor-pointer items-center justify-between gap-3 text-sm font-medium">
                        <span>Active scanning enabled for the workspace</span>
                        <input type="checkbox" className="h-[18px] w-[18px] accent-ink"
                            checked={ws.active_scan_enabled}
                            disabled={!ws.can_edit || ws.forced_by_deployment || toggleWs.isPending}
                            onChange={(e) => toggleWs.mutate(e.target.checked)} />
                    </label>
                    <p className="text-xs leading-relaxed text-ink-soft">
                        {ws.forced_by_deployment
                            ? 'Forced on by the deployment (SECURITY_ACTIVE_SCAN_ENABLED).'
                            : ws.can_edit
                                ? 'Workspace-wide master switch for attacking scans. Both this and the per-project switch must be on. Only scan targets you own.'
                                : 'Only a workspace admin can change this. Active scans need this on plus the per-project switch.'}
                    </p>
                </div>
            )}

            <div className="flex flex-col gap-2">
                <Eyebrow>Authorized domains</Eyebrow>
                <div className="flex flex-wrap gap-1.5">
                    {s.allowed_domains.length === 0 && <span className="text-xs text-ink-soft">None yet, so every scan will be refused.</span>}
                    {s.allowed_domains.map((d) => (
                        <span key={d} className="inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-1 font-mono text-xs">
                            {d}
                            <button type="button" aria-label={`Remove ${d}`} className="text-ink-soft hover:text-ink"
                                onClick={() => setS({ ...s, allowed_domains: s.allowed_domains.filter((x) => x !== d) })}>
                                <X className="h-3 w-3" />
                            </button>
                        </span>
                    ))}
                </div>
                <div className="flex gap-2">
                    <Input placeholder="example.com" value={domainInput} aria-label="Add a domain"
                        onChange={(e) => setDomainInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && addDomain()} />
                    <Button variant="outline" size="icon" onClick={addDomain} aria-label="Add domain"><Plus className="h-4 w-4" /></Button>
                </div>
            </div>
            <Button className="self-start" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
        </section>
    );
}

function SettingsCard({ projectId, workspaceId }: { projectId: number; workspaceId: number | null }) {
    const { data, dataUpdatedAt } = useQuery({ queryKey: ['sec-settings', projectId], queryFn: () => securityApi.getSettings(projectId) });
    if (!data) return null;
    // Re-key on every fetch so the form resets to what the server saved.
    return <SettingsForm key={`${projectId}-${dataUpdatedAt}`} projectId={projectId} workspaceId={workspaceId} initial={data} />;
}

const AUTH_PRESETS: Record<string, { name: string; placeholder: string; wrap: (v: string) => string }> = {
    bearer: { name: 'Authorization', placeholder: 'JWT / access token', wrap: (v) => `Bearer ${v}` },
    apikey: { name: 'X-API-Key', placeholder: 'API key', wrap: (v) => v },
    custom: { name: '', placeholder: 'header value', wrap: (v) => v },
};

function NewScanCard({ projectId, onQueued }: { projectId: number; onQueued: () => void }) {
    const qc = useQueryClient();
    const [target, setTarget] = useState('');
    const [scanType, setScanType] = useState('baseline');
    const [authenticated, setAuthenticated] = useState(false);
    const [authorized, setAuthorized] = useState(false);
    const [advanced, setAdvanced] = useState(false);
    const [openapiUrl, setOpenapiUrl] = useState('');
    const [authMode, setAuthMode] = useState<'none' | 'bearer' | 'apikey' | 'custom'>('none');
    const [tokenValue, setTokenValue] = useState('');
    const [customHeader, setCustomHeader] = useState('');

    const preset = authMode === 'none' ? null : AUTH_PRESETS[authMode];
    const headerName = authMode === 'custom' ? customHeader.trim() : preset?.name;
    const headerValue = preset && tokenValue.trim() ? preset.wrap(tokenValue.trim()) : null;
    const authIncomplete = authMode !== 'none' && (!tokenValue.trim() || (authMode === 'custom' && !customHeader.trim()));

    const run = useMutation({
        mutationFn: () => securityApi.createScan(projectId, {
            target_url: target, scan_type: scanType, authenticated, authorized,
            openapi_url: openapiUrl.trim() || null,
            auth_header_name: headerValue ? headerName : null,
            auth_header_value: headerValue,
        }),
        onSuccess: () => {
            toast.success('Scan queued');
            qc.invalidateQueries({ queryKey: ['sec-scans', projectId] });
            onQueued();
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <section className="flex max-w-3xl flex-col gap-4 rounded-3xl border border-border bg-white p-6">
            <h2 className="text-base font-semibold text-ink">New scan</h2>
            <div className="flex flex-col gap-1.5">
                <label htmlFor="scan-target" className="text-[13px] font-semibold">Target URL</label>
                <Input id="scan-target" placeholder="https://staging.example.com" value={target} onChange={(e) => setTarget(e.target.value)} />
            </div>
            <div className="flex flex-wrap items-center gap-3">
                <Select value={scanType} onValueChange={setScanType}>
                    <SelectTrigger className="h-10 min-w-[200px] flex-1 rounded-full" aria-label="Scan type"><SelectValue /></SelectTrigger>
                    <SelectContent>
                        <SelectItem value="baseline">Baseline (passive)</SelectItem>
                        <SelectItem value="active">Active (attacking)</SelectItem>
                    </SelectContent>
                </Select>
                <label className="flex cursor-pointer items-center gap-2 whitespace-nowrap text-sm">
                    <input type="checkbox" className="h-[18px] w-[18px] accent-ink" checked={authenticated} onChange={(e) => setAuthenticated(e.target.checked)} /> Use saved session
                </label>
            </div>

            <button type="button" onClick={() => setAdvanced((a) => !a)} aria-expanded={advanced}
                className="flex items-center gap-1.5 self-start text-[13px] font-semibold text-ink-soft hover:text-ink">
                {advanced ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                API import and token auth
                {(openapiUrl || authMode !== 'none') && <Pill tone="lavender" className="ml-1">configured</Pill>}
            </button>

            {advanced && (
                <div className="flex flex-col gap-4 rounded-2xl bg-background p-4">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="openapi-url" className="text-[13px] font-semibold">OpenAPI / Swagger spec URL</label>
                        <Input id="openapi-url" placeholder="https://api.example.com/openapi.json" value={openapiUrl} onChange={(e) => setOpenapiUrl(e.target.value)} />
                        <p className="text-xs text-ink-soft">Imports every documented endpoint, so the scan reaches routes the crawler can't find by following links.</p>
                    </div>
                    <div className="flex flex-col gap-1.5">
                        <span className="text-[13px] font-semibold">Token or header auth</span>
                        <div className="flex flex-wrap gap-2">
                            <Select value={authMode} onValueChange={(v) => setAuthMode(v as typeof authMode)}>
                                <SelectTrigger className="h-10 w-36 rounded-full" aria-label="Auth type"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="none">None</SelectItem>
                                    <SelectItem value="bearer">Bearer</SelectItem>
                                    <SelectItem value="apikey">API key</SelectItem>
                                    <SelectItem value="custom">Custom header</SelectItem>
                                </SelectContent>
                            </Select>
                            {authMode === 'custom' && (
                                <Input placeholder="Header-Name" aria-label="Header name" value={customHeader} onChange={(e) => setCustomHeader(e.target.value)} className="w-36" />
                            )}
                            {authMode !== 'none' && (
                                <Input type="password" autoComplete="off" aria-label="Token value" placeholder={preset?.placeholder}
                                    value={tokenValue} onChange={(e) => setTokenValue(e.target.value)} className="min-w-[160px] flex-1" />
                            )}
                        </div>
                        {headerValue && (
                            <p className="truncate font-mono text-xs text-ink-soft">Sends: {headerName}: {authMode === 'bearer' ? 'Bearer ••••••' : '••••••'}</p>
                        )}
                        <p className="text-xs text-ink-soft">Sent on every request. Kept only for this scan, then discarded.</p>
                    </div>
                </div>
            )}

            <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-border p-4 text-sm shadow-[inset_4px_0_0_#F2C94C]">
                <input type="checkbox" className="mt-0.5 h-[18px] w-[18px] accent-ink" checked={authorized} onChange={(e) => setAuthorized(e.target.checked)} />
                I am authorized to scan this target (it belongs to me or my organization).
            </label>
            <Button className="self-start" onClick={() => run.mutate()} disabled={run.isPending || !target || !authorized || authIncomplete}>
                <Play className="h-4 w-4" /> Run scan
            </Button>
        </section>
    );
}

// --- finding detail ------------------------------------------------------------

// Minimal inline markdown -> JSX for finding bodies (**bold**, `code`, links).
function inlineMd(text: string): ReactNode[] {
    const nodes: ReactNode[] = [];
    const re = /\*\*(.+?)\*\*|`([^`]+?)`|(https?:\/\/[^\s]+)/g;
    let last = 0, m: RegExpExecArray | null, i = 0;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) nodes.push(text.slice(last, m.index));
        if (m[1]) nodes.push(<strong key={i++} className="font-semibold">{m[1]}</strong>);
        else if (m[2]) nodes.push(<code key={i++} className="rounded bg-secondary px-1 py-0.5 font-mono text-[13px]">{m[2]}</code>);
        else if (m[3]) nodes.push(<a key={i++} href={m[3]} target="_blank" rel="noreferrer" className="break-all underline underline-offset-2">{m[3]}</a>);
        last = m.index + m[0].length;
    }
    if (last < text.length) nodes.push(text.slice(last));
    return nodes;
}

function FindingBody({ text }: { text: string }) {
    return (
        <div className="flex flex-col gap-1.5">
            {text.split('\n').map((line, i) => {
                const t = line.trim();
                if (!t) return null;
                if (t.startsWith('- ')) return <div key={i} className="flex gap-1.5 pl-1"><span className="text-ink-soft">•</span><span className="min-w-0 break-words">{inlineMd(t.slice(2))}</span></div>;
                return <p key={i} className="break-words">{inlineMd(t)}</p>;
            })}
        </div>
    );
}

function FindingDetail({ finding, scan, isNew, onTriage, busy }: {
    finding: SecurityFinding; scan: SecurityScan; isNew: boolean;
    onTriage: (status: FindingStatus) => void; busy: boolean;
}) {
    const status = (finding.status || 'open') as FindingStatus;
    const next: { status: FindingStatus; label: string; primary?: boolean }[] =
        status === 'open'
            ? [{ status: 'acknowledged', label: 'Acknowledge', primary: true }, { status: 'false_positive', label: 'Not a real issue' }, { status: 'resolved', label: 'Mark resolved' }]
            : [{ status: 'open', label: 'Reopen', primary: true }, ...(status !== 'resolved' ? [{ status: 'resolved' as FindingStatus, label: 'Mark resolved' }] : [])];
    return (
        <DetailCard label="Selected finding">
            <div className="flex flex-col gap-2">
                <div className="flex flex-wrap items-center gap-2">
                    <Pill tone={SEV_TONE[finding.severity]} className="capitalize">{finding.severity}</Pill>
                    {isNew && <Pill tone="lavender">New since the previous scan</Pill>}
                    <span className="text-[13px] text-ink-soft">{STATUS_LABEL[status]} · {scan.scan_type} scan #{scan.id} · {ago(finding.created_at)}</span>
                </div>
                <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{finding.title}</h2>
                {finding.target_url && <span className="break-all font-mono text-[13px] text-ink-soft">{finding.target_url}</span>}
            </div>

            {finding.description && <Panel title="Why it matters and how to fix it" callout><FindingBody text={finding.description} /></Panel>}

            {finding.evidence && (
                <div className="flex flex-col gap-2">
                    <Eyebrow>Evidence</Eyebrow>
                    <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-2xl border border-border p-4 font-mono text-[13px] leading-relaxed">{finding.evidence}</pre>
                </div>
            )}

            <div className="flex flex-wrap gap-2.5">
                {next.map((n) => (
                    <Button key={n.status} size="lg" variant={n.primary ? 'default' : 'outline'} disabled={busy} onClick={() => onTriage(n.status)}>
                        {n.label}
                    </Button>
                ))}
            </div>
        </DetailCard>
    );
}

function Tile({ label, value, tone }: { label: string; value: number | string; tone?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className={cn('text-[28px] font-bold leading-none tracking-tight tabular-nums text-ink', tone)}>{value}</span>
        </div>
    );
}

// --- page ------------------------------------------------------------------------

export default function Security() {
    const projectId = useActiveProjectId();
    const qc = useQueryClient();
    const [panel, setPanel] = useState<'none' | 'settings' | 'scan'>('none');
    const [scanId, setScanId] = useState<number | null>(null);
    const [tab, setTab] = useState<FindingStatus>('open');
    const [findingId, setFindingId] = useState<number | null>(null);

    const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: () => getProjects() });
    const workspaceId = (projects || []).find((p) => p.id === projectId)?.workspace_id ?? null;

    const scans = useQuery({
        queryKey: ['sec-scans', projectId],
        queryFn: () => securityApi.listScans(projectId!),
        enabled: !!projectId,
        refetchInterval: 15000,
    });
    const scanList = [...(scans.data ?? [])].sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
    const selectedScan = scanList.find((s) => s.id === scanId)
        ?? scanList.find((s) => s.status === 'completed') ?? scanList[0] ?? null;

    const detail = useQuery({
        queryKey: ['sec-scan', selectedScan?.id],
        queryFn: () => securityApi.getScan(selectedScan!.id),
        enabled: !!selectedScan,
    });
    const diff = useQuery({
        queryKey: ['sec-scan-diff', selectedScan?.id],
        queryFn: () => securityApi.scanDiff(selectedScan!.id),
        enabled: selectedScan?.status === 'completed',
    });
    const triage = useMutation({
        mutationFn: ({ id, status }: { id: number; status: string }) => securityApi.updateFinding(id, { status }),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ['sec-scan', selectedScan?.id] });
            qc.invalidateQueries({ queryKey: ['inbox'] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    const findings = detail.data?.findings ?? selectedScan?.findings ?? [];
    const statusOf = (f: SecurityFinding) => (f.status || 'open') as FindingStatus;
    const sevRank: Record<string, number> = { high: 0, medium: 1, low: 2, info: 3 };
    const inTab = findings.filter((f) => statusOf(f) === tab)
        .sort((a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9));
    const selectedFinding = inTab.find((f) => f.id === findingId) ?? inTab[0] ?? null;
    const newIds = new Set((diff.data?.new ?? []).map((n) => n.id));

    const togglePanel = (p: 'settings' | 'scan') => setPanel((cur) => (cur === p ? 'none' : p));

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Security"
                subtitle={
                    selectedScan
                        ? <>Scan #{selectedScan.id} of <span className="font-mono">{selectedScan.target_url}</span>{diff.data?.baseline_available ? `, compared with scan #${diff.data.previous_scan_id}` : ''}</>
                        : 'OWASP ZAP scans of targets you are authorized to test. Baseline is passive; active scans attack the target.'
                }
                actions={
                    projectId ? (
                        <>
                            <Button variant="outline" onClick={() => togglePanel('settings')} aria-expanded={panel === 'settings'}>Authorization</Button>
                            <Button onClick={() => togglePanel('scan')} aria-expanded={panel === 'scan'}>New scan</Button>
                        </>
                    ) : undefined
                }
            />

            {!projectId ? (
                <EmptyState title="Pick a project">Choose a project in the sidebar to manage its scans.</EmptyState>
            ) : (
                <>
                    {panel === 'settings' && <SettingsCard projectId={projectId} workspaceId={workspaceId} />}
                    {panel === 'scan' && <NewScanCard projectId={projectId} onQueued={() => setPanel('none')} />}

                    {scans.isLoading ? (
                        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
                            {[0, 1, 2].map((i) => <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />)}
                        </div>
                    ) : scans.error ? (
                        <EmptyState title="Couldn't load scans">{errDetail(scans.error)}</EmptyState>
                    ) : scanList.length === 0 ? (
                        <EmptyState title="No scans yet">Set up authorization, then run a scan. Its findings appear here, compared with the scan before.</EmptyState>
                    ) : (
                        <>
                            <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
                                <Tile label="New" value={diff.data?.baseline_available ? diff.data.new.length : '—'} tone={diff.data?.new.length ? 'text-pastel-ink-peach' : undefined} />
                                <Tile label="Fixed since the previous scan" value={diff.data?.baseline_available ? diff.data.fixed.length : '—'} tone={diff.data?.fixed.length ? 'text-pastel-ink-mint' : undefined} />
                                <Tile label="Still open" value={findings.filter((f) => statusOf(f) === 'open').length} />
                                <Tile label="Acknowledged" value={findings.filter((f) => statusOf(f) === 'acknowledged').length} />
                            </div>

                            <SplitView
                                list={
                                    <>
                                        <div role="tablist" aria-label="Finding status" className="grid grid-cols-2 gap-1 rounded-[20px] bg-[#EFEDE7] p-1">
                                            {STATUSES.map((s) => {
                                                const n = findings.filter((f) => statusOf(f) === s).length;
                                                return (
                                                    <button key={s} type="button" role="tab" aria-selected={tab === s}
                                                        onClick={() => { setTab(s); setFindingId(null); }}
                                                        className={cn('flex min-h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-full px-2 text-[13px] text-ink',
                                                            tab === s ? 'bg-white font-semibold shadow-[0_1px_4px_rgba(28,27,34,0.1)]' : 'font-medium')}>
                                                        {STATUS_LABEL[s]}<span className="font-mono text-xs text-ink-soft">{n}</span>
                                                    </button>
                                                );
                                            })}
                                        </div>
                                        <div className="flex flex-col gap-0.5">
                                            {detail.isLoading && !findings.length ? (
                                                <div className="flex justify-center p-6"><RefreshCw className="h-4 w-4 animate-spin text-ink-soft" /></div>
                                            ) : inTab.length === 0 ? (
                                                <p className="px-2 py-6 text-center text-sm text-ink-soft">Nothing {STATUS_LABEL[tab].toLowerCase()} in this scan.</p>
                                            ) : inTab.map((f) => (
                                                <ListRow
                                                    key={f.id}
                                                    selected={selectedFinding?.id === f.id}
                                                    onSelect={() => setFindingId(f.id)}
                                                    leading={<Pill tone={SEV_TONE[f.severity]} className="min-w-[64px] justify-center capitalize">{f.severity}</Pill>}
                                                    title={f.title}
                                                    meta={[newIds.has(f.id) ? 'New' : null, f.target_url].filter(Boolean).join(' · ')}
                                                />
                                            ))}
                                        </div>
                                        <ListGroup title="Scans" count={scanList.length}>
                                            {scanList.slice(0, 8).map((s) => {
                                                const c = s.counts || {};
                                                const summary = (['high', 'medium', 'low'] as const).filter((k) => c[k]).map((k) => `${c[k]} ${k}`).join(' · ');
                                                return (
                                                    <ListRow
                                                        key={s.id}
                                                        selected={selectedScan?.id === s.id}
                                                        onSelect={() => { setScanId(s.id); setFindingId(null); }}
                                                        leading={<Pill tone={SCAN_TONE[s.status] ?? 'neutral'} className="min-w-[84px] justify-center capitalize">{s.status}</Pill>}
                                                        title={<span className="font-mono text-[13px]">{s.target_url}</span>}
                                                        meta={[`#${s.id} · ${s.scan_type}${s.authenticated ? ' · session' : ''}`, ago(s.created_at), summary || null, s.openapi_url ? 'API import' : null, s.auth_header_name].filter(Boolean).join(' · ')}
                                                    />
                                                );
                                            })}
                                        </ListGroup>
                                    </>
                                }
                                detail={
                                    selectedScan?.error ? (
                                        <EmptyState title="This scan failed">{selectedScan.error}</EmptyState>
                                    ) : selectedFinding && selectedScan ? (
                                        <FindingDetail
                                            finding={selectedFinding}
                                            scan={selectedScan}
                                            isNew={newIds.has(selectedFinding.id)}
                                            busy={triage.isPending}
                                            onTriage={(status) => triage.mutate({ id: selectedFinding.id, status })}
                                        />
                                    ) : (
                                        <EmptyState title={selectedScan?.status === 'completed' ? 'Nothing to show' : 'Scan in progress'}>
                                            {selectedScan?.status === 'completed'
                                                ? 'Pick another tab, or another scan below.'
                                                : 'Findings appear here when the scan finishes. This page refreshes every 15 seconds.'}
                                        </EmptyState>
                                    )
                                }
                            />
                        </>
                    )}
                </>
            )}
        </div>
    );
}
