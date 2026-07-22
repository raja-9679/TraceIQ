import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Ticket, Plus, Trash2, RefreshCw, AlertCircle, Building2, CheckCircle2, XCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, Workspace } from '@/lib/api';
import { ticketsApi, IssueTrackerConfig, IssueTrackerConfigCreate } from '@/api/tickets';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const PROVIDER_HINTS: Record<string, { user: string; secret: string; settings: string }> = {
    jira: { user: 'Jira email', secret: 'API token', settings: 'project_key, issue_type' },
    itop: { user: 'iTop username', secret: 'Password', settings: 'class (UserRequest), org_id' },
    github: { user: '(unused)', secret: 'Personal access token', settings: 'repo (owner/name)' },
};

function NewConfigForm({ workspaceId, onDone }: { workspaceId: number; onDone: () => void }) {
    const qc = useQueryClient();
    const [f, setF] = useState<IssueTrackerConfigCreate>({
        provider: 'jira', name: '', base_url: '', auth_user: '', auth_secret: '', settings: {}, enabled: true,
    });
    const [settingsText, setSettingsText] = useState('{\n  "project_key": "PROJ",\n  "issue_type": "Bug"\n}');

    const create = useMutation({
        mutationFn: () => {
            let settings = {};
            try { settings = settingsText.trim() ? JSON.parse(settingsText) : {}; }
            catch { throw new Error('Settings must be valid JSON'); }
            return ticketsApi.createConfig(workspaceId, { ...f, settings });
        },
        onSuccess: () => { toast.success('Tracker added'); qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }); onDone(); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const hint = PROVIDER_HINTS[f.provider];
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-3">
            <h3 className="font-bold text-slate-800">New tracker</h3>
            <div className="grid sm:grid-cols-2 gap-3">
                <div>
                    <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Provider</label>
                    <Select value={f.provider} onValueChange={(v) => setF({ ...f, provider: v })}>
                        <SelectTrigger className="h-9 mt-1"><SelectValue /></SelectTrigger>
                        <SelectContent>
                            <SelectItem value="jira">Jira</SelectItem>
                            <SelectItem value="itop">iTop</SelectItem>
                            <SelectItem value="github">GitHub Issues</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
                <div>
                    <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Name</label>
                    <Input className="h-9 mt-1" placeholder="Team Jira" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
                </div>
            </div>
            <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Base URL</label>
                <Input className="h-9 mt-1" placeholder={f.provider === 'github' ? 'https://api.github.com' : 'https://your.instance'} value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} />
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
                <div>
                    <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{hint.user}</label>
                    <Input className="h-9 mt-1" value={f.auth_user} onChange={(e) => setF({ ...f, auth_user: e.target.value })} disabled={f.provider === 'github'} />
                </div>
                <div>
                    <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{hint.secret}</label>
                    <Input className="h-9 mt-1" type="password" placeholder="stored encrypted" value={f.auth_secret} onChange={(e) => setF({ ...f, auth_secret: e.target.value })} />
                </div>
            </div>
            <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Settings JSON <span className="text-slate-300 normal-case">({hint.settings})</span></label>
                <textarea className="w-full h-24 font-mono text-xs border border-slate-200 rounded-lg p-2 mt-1" value={settingsText} onChange={(e) => setSettingsText(e.target.value)} />
            </div>
            <div className="flex gap-2">
                <Button size="sm" className="h-9 rounded-lg" onClick={() => create.mutate()} disabled={create.isPending || !f.name || !f.base_url || !f.auth_secret}>Add tracker</Button>
                <Button size="sm" variant="outline" className="h-9 rounded-lg" onClick={onDone}>Cancel</Button>
            </div>
        </div>
    );
}

export default function IssueTrackers() {
    const qc = useQueryClient();
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const [adding, setAdding] = useState(false);

    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const workspaceId = selectedWs ?? workspaces?.[0]?.id ?? null;

    const configs = useQuery({
        queryKey: ['trackers', workspaceId],
        queryFn: () => ticketsApi.listConfigs(workspaceId!),
        enabled: !!workspaceId,
    });

    const del = useMutation({
        mutationFn: (id: number) => ticketsApi.deleteConfig(workspaceId!, id),
        onSuccess: () => { toast.success('Tracker removed'); qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const toggle = useMutation({
        mutationFn: (c: IssueTrackerConfig) => ticketsApi.updateConfig(workspaceId!, c.id, { enabled: !c.enabled }),
        onSuccess: () => qc.invalidateQueries({ queryKey: ['trackers', workspaceId] }),
        onError: (e) => toast.error(errDetail(e)),
    });

    return (
        <div className="max-w-[1000px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Integrations</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Issue Trackers</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Connect Jira, iTop or GitHub so you can file a ticket from a failing run — with its
                            trace, video and screenshots attached — in one click.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Workspace</p>
                        <Select value={workspaceId?.toString() ?? ''} onValueChange={(v) => setSelectedWs(parseInt(v))}>
                            <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                                <div className="flex items-center gap-2 min-w-0"><Building2 className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Workspace" /></div>
                            </SelectTrigger>
                            <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!workspaceId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <p className="text-slate-500 text-sm">No workspace available.</p>
                </div>
            ) : (
                <div className="space-y-4">
                    {!adding && <Button size="sm" className="h-9 rounded-lg" onClick={() => setAdding(true)}><Plus className="w-4 h-4 mr-1.5" /> Add tracker</Button>}
                    {adding && <NewConfigForm workspaceId={workspaceId} onDone={() => setAdding(false)} />}

                    {configs.isLoading ? <div className="p-8 flex justify-center"><RefreshCw className="animate-spin w-5 h-5 text-slate-400" /></div>
                        : !configs.data || configs.data.length === 0 ? (
                            <div className="p-12 text-center bg-white border border-slate-200 rounded-2xl">
                                <Ticket className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                                <p className="text-slate-500 text-sm">No trackers yet. Add one to start filing tickets from runs.</p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {configs.data.map((c) => (
                                    <div key={c.id} className="bg-white border border-slate-200 rounded-xl p-4 flex items-center justify-between">
                                        <div>
                                            <div className="flex items-center gap-2">
                                                <span className="font-semibold text-slate-800">{c.name}</span>
                                                <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-slate-50 text-slate-600 border-slate-200">{c.provider}</Badge>
                                                {c.enabled
                                                    ? <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-emerald-50 text-emerald-700 border-emerald-200"><CheckCircle2 className="w-3 h-3 mr-1" />enabled</Badge>
                                                    : <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-slate-50 text-slate-500 border-slate-200"><XCircle className="w-3 h-3 mr-1" />disabled</Badge>}
                                            </div>
                                            <div className="text-xs text-slate-400 mt-0.5">{c.base_url}{c.auth_user ? ` · ${c.auth_user}` : ''}</div>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <Button size="sm" variant="outline" className="h-8 rounded-lg text-xs" onClick={() => toggle.mutate(c)}>{c.enabled ? 'Disable' : 'Enable'}</Button>
                                            <Button size="sm" variant="outline" className="h-8 rounded-lg text-xs text-rose-600 border-rose-200 hover:bg-rose-50" onClick={() => del.mutate(c.id)}><Trash2 className="w-3.5 h-3.5" /></Button>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                </div>
            )}
        </div>
    );
}
