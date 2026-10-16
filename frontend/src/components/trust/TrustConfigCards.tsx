import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Activity, Check, Copy, Globe, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, FieldRow, SettingsCard, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { trustApi, type TrustCenterConfig, type TrustCenterInput } from '@/api/trustCenter';
import { shareUrl } from '@/components/reports/reportHelpers';
import { POSTURE_TONE, errDetail } from './trustHelpers';


function useSave(workspaceId: number) {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (body: TrustCenterInput) => trustApi.save(workspaceId, body),
        onSuccess: (cfg) => { qc.setQueryData(['trust-center', workspaceId], cfg); toast.success('Saved'); },
        onError: (e) => toast.error(errDetail(e)),
    });
}

function PublicLink({ path }: { path: string }) {
    const [copied, setCopied] = useState(false);
    const url = shareUrl(path);
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
        <div className="flex w-full flex-wrap gap-2">
            <Input aria-label="Public trust center link" readOnly value={url} className={`${inputClass} min-w-0 flex-1 font-mono text-xs`}
                onFocus={(e) => e.currentTarget.select()} />
            <Button variant="outline" onClick={copy}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} {copied ? 'Copied' : 'Copy link'}</Button>
        </div>
    );
}

export function TrustGeneralCard({ cfg }: { cfg: TrustCenterConfig }) {
    const save = useSave(cfg.workspace_id);
    const [enabled, setEnabled] = useState(cfg.enabled);
    const [title, setTitle] = useState(cfg.title);
    const [intro, setIntro] = useState(cfg.intro);
    const [email, setEmail] = useState(cfg.contact_email ?? '');
    const [requests, setRequests] = useState(cfg.allow_access_requests);
    const body = { enabled, title, intro, contact_email: email, allow_access_requests: requests };
    return (
        <SettingsCard
            labelledBy="trust-general"
            icon={<Globe className="h-4 w-4" />}
            title="Public page"
            description="Off by default. When on, anyone with the link sees only what you publish below. The link is a long random identifier; generate a new one to retire the old link."
            actions={<Pill tone={cfg.enabled ? 'mint' : 'neutral'}>{cfg.enabled ? 'Published' : 'Off'}</Pill>}
            footer={<>
                <Button onClick={() => save.mutate(body)} disabled={save.isPending}>Save</Button>
                {cfg.configured && (
                    <Button variant="outline" disabled={save.isPending}
                        onClick={() => { if (window.confirm('Retire the current public link? People with the old link will see "not found".')) save.mutate({ regenerate_slug: true }); }}>
                        New public link
                    </Button>
                )}
            </>}
        >
            <div className="flex flex-col">
                <CheckRow id="trust-enabled" label="Publish the trust center" hint="Turning it off hides the page and stops every document link at once." checked={enabled} onChange={setEnabled} />
                <FieldRow label="Title" htmlFor="trust-title" wide>
                    <Input id="trust-title" className={inputClass} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />
                </FieldRow>
                <FieldRow label="Security contact email" htmlFor="trust-email" hint="Shown on the page for security reports." wide>
                    <Input id="trust-email" type="email" className={inputClass} maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="security@example.com" />
                </FieldRow>
                <div className="flex flex-col gap-2 py-2">
                    <label htmlFor="trust-intro" className="text-sm font-medium text-ink">Introduction</label>
                    <textarea id="trust-intro" rows={4} maxLength={4000} className={`${textareaClass} font-sans`} value={intro} onChange={(e) => setIntro(e.target.value)}
                        placeholder="How we protect your data, who to contact, what this page covers." />
                    <p className="text-xs text-ink-soft">Plain text. Shown as written; no HTML.</p>
                </div>
                <CheckRow id="trust-requests" label="Let visitors request document access"
                    hint="A short form (rate-limited, no account needed) that emails this workspace's admins. You then issue a link yourself."
                    checked={requests} onChange={setRequests} />
            </div>
            {cfg.path && (
                <div className="flex flex-col gap-2 border-t border-border pt-3">
                    <span className="text-sm font-medium text-ink">Public link</span>
                    <PublicLink path={cfg.path} />
                    <span className="text-xs text-ink-soft">{cfg.view_count} view{cfg.view_count === 1 ? '' : 's'}{cfg.last_viewed_at ? ` · last opened ${new Date(cfg.last_viewed_at).toLocaleString()}` : ''}</span>
                </div>
            )}
        </SettingsCard>
    );
}

export function TrustStatusCard({ cfg }: { cfg: TrustCenterConfig }) {
    const save = useSave(cfg.workspace_id);
    const [ids, setIds] = useState<number[]>(cfg.status_page_ids);
    const toggle = (id: number, on: boolean) => setIds((p) => (on ? [...p, id] : p.filter((x) => x !== id)));
    return (
        <SettingsCard
            labelledBy="trust-status"
            icon={<Activity className="h-4 w-4" />}
            title="Uptime"
            description="Monitors from the status pages you pick, as name, up/down and 24-hour / 7-day uptime. Never target URLs or run details. A status page that is switched off drops out by itself."
            footer={<Button onClick={() => save.mutate({ status_page_ids: ids })} disabled={save.isPending}>Save</Button>}
        >
            {cfg.available_status_pages.length === 0 ? (
                <p className="text-sm text-ink-soft">No project in this workspace has a status page yet. Create one from a project's monitors.</p>
            ) : (
                <div className="flex flex-col">
                    {cfg.available_status_pages.map((p) => (
                        <CheckRow key={p.id} id={`trust-sp-${p.id}`} label={`${p.project_name} · ${p.title}`}
                            hint={p.enabled ? undefined : 'This status page is switched off, so it shows nothing.'}
                            checked={ids.includes(p.id)} onChange={(v) => toggle(p.id, v)} />
                    ))}
                </div>
            )}
        </SettingsCard>
    );
}

export function TrustPostureCard({ cfg }: { cfg: TrustCenterConfig }) {
    const qc = useQueryClient();
    const save = useSave(cfg.workspace_id);
    const [keys, setKeys] = useState<string[]>(cfg.posture_items);
    const toggle = (k: string, on: boolean) => setKeys((p) => (on ? [...p, k] : p.filter((x) => x !== k)));
    const verify = useMutation({
        mutationFn: () => trustApi.verifyAudit(cfg.workspace_id),
        onSuccess: (r) => {
            toast[r.verified ? 'success' : 'error'](r.verified ? `Audit chain verified (${r.rows_checked} rows)` : 'The audit chain did not verify');
            qc.invalidateQueries({ queryKey: ['trust-center', cfg.workspace_id] });
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="trust-posture"
            icon={<ShieldCheck className="h-4 w-4" />}
            title="Security posture"
            description="Aggregate statements only, each published only if you tick it. Items the product has no data for stay hidden even when ticked. Finding counts are shown as a band, never with titles or details."
            footer={<>
                <Button onClick={() => save.mutate({ posture_items: keys })} disabled={save.isPending}>Save</Button>
                <Button variant="outline" onClick={() => verify.mutate()} disabled={verify.isPending || !cfg.configured}>Verify audit chain now</Button>
            </>}
        >
            <ul className="flex flex-col">
                {cfg.posture_preview.map((item, i) => (
                    <li key={item.key} className={`flex flex-col gap-1 py-2 ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                        <CheckRow id={`trust-posture-${item.key}`} label={item.label} hint={item.hint}
                            checked={keys.includes(item.key)} onChange={(v) => toggle(item.key, v)} />
                        <span className="flex items-center gap-2 text-xs text-ink-soft">
                            Would show:
                            {item.available && item.value
                                ? <Pill tone={item.state ? POSTURE_TONE[item.state] : 'neutral'}>{item.value}</Pill>
                                : <span>hidden: no data yet</span>}
                        </span>
                    </li>
                ))}
            </ul>
        </SettingsCard>
    );
}
