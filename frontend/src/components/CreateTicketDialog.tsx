import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Ticket, ExternalLink, RefreshCw, CheckCircle2, XCircle } from 'lucide-react';
import {
    Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces } from '@/lib/api';
import { ticketsApi } from '@/api/tickets';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

export default function CreateTicketDialog({ runId }: { runId: number }) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const [wsId, setWsId] = useState<number | null>(null);
    const [configId, setConfigId] = useState<string>('');
    const [summary, setSummary] = useState('');
    const [attachTrace, setAttachTrace] = useState(true);
    const [attachVideo, setAttachVideo] = useState(true);
    const [attachShots, setAttachShots] = useState(true);

    const { data: workspaces } = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces, enabled: open });
    const workspaceId = wsId ?? workspaces?.[0]?.id ?? null;

    const configs = useQuery({
        queryKey: ['trackers', workspaceId],
        queryFn: () => ticketsApi.listConfigs(workspaceId!),
        enabled: open && !!workspaceId,
    });
    const tickets = useQuery({
        queryKey: ['run-tickets', runId],
        queryFn: () => ticketsApi.listTickets(runId),
        enabled: open,
        refetchInterval: open ? 4000 : false,
    });

    const create = useMutation({
        mutationFn: () => ticketsApi.createTicket(runId, {
            config_id: parseInt(configId), summary: summary || undefined,
            attach_trace: attachTrace, attach_video: attachVideo, attach_screenshots: attachShots,
        }),
        onSuccess: () => { toast.success('Ticket queued — creating & uploading artifacts'); qc.invalidateQueries({ queryKey: ['run-tickets', runId] }); setSummary(''); },
        onError: (e) => toast.error(errDetail(e)),
    });

    const enabledConfigs = (configs.data || []).filter((c) => c.enabled);

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button size="sm" variant="outline" className="h-9 rounded-lg gap-1.5"><Ticket className="w-4 h-4" /> Create ticket</Button>
            </DialogTrigger>
            <DialogContent className="max-w-lg">
                <DialogHeader><DialogTitle>Create ticket from run #{runId}</DialogTitle></DialogHeader>

                <div className="space-y-3">
                    {(workspaces?.length ?? 0) > 1 && (
                        <Select value={workspaceId?.toString() ?? ''} onValueChange={(v) => setWsId(parseInt(v))}>
                            <SelectTrigger className="h-9"><SelectValue placeholder="Workspace" /></SelectTrigger>
                            <SelectContent>{workspaces!.map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                    )}

                    {enabledConfigs.length === 0 ? (
                        <p className="text-sm text-slate-500 bg-amber-50/60 border border-amber-100 rounded-lg px-3 py-2">
                            No enabled trackers in this workspace. Add one under <span className="font-semibold">Issue Trackers</span> first.
                        </p>
                    ) : (
                        <>
                            <div>
                                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Tracker</label>
                                <Select value={configId} onValueChange={setConfigId}>
                                    <SelectTrigger className="h-9 mt-1"><SelectValue placeholder="Select a tracker" /></SelectTrigger>
                                    <SelectContent>{enabledConfigs.map((c) => <SelectItem key={c.id} value={c.id.toString()}>{c.name} ({c.provider})</SelectItem>)}</SelectContent>
                                </Select>
                            </div>
                            <div>
                                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Summary <span className="text-slate-300 normal-case">(blank = auto)</span></label>
                                <Input className="h-9 mt-1" placeholder="[TraceIQ] …" value={summary} onChange={(e) => setSummary(e.target.value)} />
                            </div>
                            <div className="flex gap-4 text-sm text-slate-600">
                                <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={attachTrace} onChange={(e) => setAttachTrace(e.target.checked)} /> Trace</label>
                                <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={attachVideo} onChange={(e) => setAttachVideo(e.target.checked)} /> Video</label>
                                <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={attachShots} onChange={(e) => setAttachShots(e.target.checked)} /> Screenshots</label>
                            </div>
                            <Button size="sm" className="h-9 rounded-lg w-full" onClick={() => create.mutate()} disabled={create.isPending || !configId}>Create ticket</Button>
                        </>
                    )}

                    {(tickets.data?.length ?? 0) > 0 && (
                        <div className="border-t border-slate-100 pt-3 space-y-1.5">
                            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Tickets for this run</div>
                            {tickets.data!.map((t) => (
                                <div key={t.id} className="flex items-center justify-between text-sm">
                                    <span className="flex items-center gap-2 min-w-0">
                                        {t.status === 'created' ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                                            : t.status === 'error' ? <XCircle className="w-3.5 h-3.5 text-rose-500 shrink-0" />
                                                : <RefreshCw className="w-3.5 h-3.5 text-slate-400 animate-spin shrink-0" />}
                                        {t.url
                                            ? <a href={t.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline truncate">{t.external_key || t.summary}</a>
                                            : <span className="text-slate-600 truncate">{t.summary}</span>}
                                    </span>
                                    <span className="text-xs text-slate-400 shrink-0 ml-2">
                                        {t.status === 'created' && t.attachments_total > 0 ? `${t.attachments_uploaded}/${t.attachments_total} files` : t.status}
                                        {t.url && <ExternalLink className="w-3 h-3 inline ml-1" />}
                                    </span>
                                </div>
                            ))}
                            {tickets.data!.some((t) => t.status === 'error') && (
                                <p className="text-xs text-rose-500">{tickets.data!.find((t) => t.status === 'error')?.error}</p>
                            )}
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    );
}
