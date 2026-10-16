import { useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, Link2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, inputClass } from '@/components/redesign-local/groupD/settingsForm';
import { shareLinksApi, type CreatedShareLink, type ShareKind, type ShareLink } from '@/api/reports';
import { EXPIRY_OPTIONS, SHARE_KIND_LABELS, SHARE_STATE, shareUrl } from './reportHelpers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';

const selectClass = 'h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink';

/** The new link, shown once: the server keeps only a hash of it. */
export function CreatedLink({ link }: { link: CreatedShareLink }) {
    const [copied, setCopied] = useState(false);
    const url = shareUrl(link.path);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(url);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch {
            toast.error('Copy failed: select the link and copy it by hand');
        }
    };
    return (
        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-white p-4 shadow-[inset_4px_0_0_#8FD3B6]">
            <label htmlFor={`share-url-${link.id}`} className="text-sm font-medium text-ink">Read-only link</label>
            <div className="flex flex-wrap gap-2">
                <Input id={`share-url-${link.id}`} readOnly value={url} className={`${inputClass} min-w-0 flex-1 font-mono text-xs`}
                    onFocus={(e) => e.currentTarget.select()} />
                <Button variant="outline" onClick={copy}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} {copied ? 'Copied' : 'Copy link'}</Button>
            </div>
            <p className="text-xs leading-relaxed text-ink-soft">
                Copy it now: it is not shown again. Anyone with the link can open it until{' '}
                {new Date(link.expires_at).toLocaleDateString()}. It shows names, statuses and counts only: no screenshots,
                traces, error messages or URLs.
            </p>
        </div>
    );
}

export function ExpirySelect({ id, value, onChange }: { id: string; value: number; onChange: (v: number) => void }) {
    return (
        <select id={id} className={selectClass} value={value} onChange={(e) => onChange(parseInt(e.target.value))}>
            {EXPIRY_OPTIONS.map((d) => <option key={d} value={d}>{d === 1 ? '1 day' : `${d} days`}</option>)}
        </select>
    );
}

export function LinkRows({ links, onRevoke, pending }: { links: ShareLink[]; onRevoke: (id: number) => void; pending: boolean }) {
    if (links.length === 0) return <p className="text-sm text-ink-soft">No links yet.</p>;
    return (
        <ul className="flex flex-col">
            {links.map((l, i) => {
                const st = SHARE_STATE[l.state];
                return (
                    <li key={l.id} className={`flex min-h-14 flex-wrap items-center justify-between gap-2 py-2 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                        <div className="flex min-w-0 flex-col">
                            <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                                {l.label || SHARE_KIND_LABELS[l.kind]}{l.kind === 'run_report' && l.target_id ? ` · run #${l.target_id}` : ''}
                                <Pill tone={st.tone}>{st.label}</Pill>
                            </span>
                            <span className="text-xs text-ink-soft">
                                {l.created_by?.full_name ? `by ${l.created_by.full_name} · ` : ''}
                                expires {new Date(l.expires_at).toLocaleDateString()} · {l.view_count} view{l.view_count === 1 ? '' : 's'}
                                {l.last_viewed_at ? ` · last opened ${new Date(l.last_viewed_at).toLocaleString()}` : ''}
                            </span>
                        </div>
                        {l.state === 'active' && (
                            <Button variant="outline" disabled={pending} onClick={() => onRevoke(l.id)}>Revoke</Button>
                        )}
                    </li>
                );
            })}
        </ul>
    );
}

/** Links to an executive summary (project or workspace scope) + every link of the project. */
export function ShareLinksCard({ scope, projectId, workspaceId, windowDays }: {
    scope: 'project' | 'workspace'; projectId?: number | null; workspaceId?: number | null; windowDays: number;
}) {
    const qc = useQueryClient();
    const [expires, setExpires] = useState(7);
    const [label, setLabel] = useState('');
    const [created, setCreated] = useState<CreatedShareLink | null>(null);
    const key = ['share-links', scope, scope === 'project' ? projectId : workspaceId];
    const enabled = scope === 'project' ? !!projectId : !!workspaceId;
    const links = useQuery({
        queryKey: key,
        queryFn: () => (scope === 'project' ? shareLinksApi.listProject(projectId!) : shareLinksApi.listWorkspace(workspaceId!, 'workspace_summary')),
        enabled,
    });
    const create = useMutation({
        mutationFn: () => {
            const kind: ShareKind = scope === 'project' ? 'project_summary' : 'workspace_summary';
            const body = { kind, expires_in_days: expires, window_days: windowDays, label: label.trim() || undefined };
            return scope === 'project' ? shareLinksApi.createProject(projectId!, body) : shareLinksApi.createWorkspace(workspaceId!, body);
        },
        onSuccess: (l) => { setCreated(l); setLabel(''); qc.invalidateQueries({ queryKey: key }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const revoke = useMutation({
        mutationFn: (id: number) => shareLinksApi.revoke(id),
        onSuccess: () => { toast.success('Link revoked'); qc.invalidateQueries({ queryKey: key }); },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="share-links"
            icon={<Link2 className="h-4 w-4" />}
            title="Share a read-only link"
            description={scope === 'project'
                ? 'A link anyone can open without signing in, to this summary as it stands when they open it. Needs editor access; stops working if you lose it.'
                : 'Covers only the projects where you are an editor. Stops working if you leave the workspace.'}
            footer={<Button onClick={() => create.mutate()} disabled={!enabled || create.isPending}>Create link</Button>}
        >
            <div className="flex flex-col">
                <FieldRow label="Expires after" htmlFor="share-expiry" hint="At most 90 days. You can revoke it any time.">
                    <ExpirySelect id="share-expiry" value={expires} onChange={setExpires} />
                </FieldRow>
                <FieldRow label="Label" htmlFor="share-label" hint="Optional, to tell links apart (for example who it is for)." wide>
                    <Input id="share-label" className={inputClass} maxLength={120} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Board update" />
                </FieldRow>
            </div>
            {created && <CreatedLink link={created} />}
            <div className="flex flex-col border-t border-border pt-3">
                <h3 className="mb-1 text-sm font-semibold text-ink">{scope === 'project' ? "This project's links" : 'Workspace summary links'}</h3>
                {links.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                    : <LinkRows links={links.data || []} onRevoke={(id) => revoke.mutate(id)} pending={revoke.isPending} />}
            </div>
        </SettingsCard>
    );
}

/** "Share" on a run page: a read-only link to this run's report. */
export function ShareRunButton({ runId, projectId, trigger }: { runId: number; projectId: number; trigger?: ReactNode }) {
    const [open, setOpen] = useState(false);
    const [expires, setExpires] = useState(7);
    const [created, setCreated] = useState<CreatedShareLink | null>(null);
    const create = useMutation({
        mutationFn: () => shareLinksApi.createProject(projectId, { kind: 'run_report', run_id: runId, expires_in_days: expires }),
        onSuccess: setCreated,
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <Dialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) setCreated(null); }}>
            <DialogTrigger asChild>
                {trigger ?? <Button variant="outline"><Link2 size={16} /> Share</Button>}
            </DialogTrigger>
            <DialogContent className="max-w-lg">
                <DialogHeader><DialogTitle>Share this run</DialogTitle></DialogHeader>
                <p className="text-sm leading-relaxed text-ink-soft">
                    A read-only page with the run's test names, statuses and timings, for people without a TraceIQ account.
                    It never shows screenshots, traces, videos, error messages or URLs.
                </p>
                {created ? <CreatedLink link={created} /> : (
                    <div className="flex flex-col gap-3">
                        <FieldRow label="Expires after" htmlFor="share-run-expiry">
                            <ExpirySelect id="share-run-expiry" value={expires} onChange={setExpires} />
                        </FieldRow>
                        <div><Button onClick={() => create.mutate()} disabled={create.isPending}>Create link</Button></div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
