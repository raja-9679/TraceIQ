import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Megaphone } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Pill } from '@/components/redesign/primitives';
import { CheckRow, FieldRow, SettingsCard, inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { trustApi, type IncidentPost, type IncidentStatus } from '@/api/trustCenter';
import { INCIDENT_STATUS, INCIDENT_STATUSES, errDetail, parseComponents } from './trustHelpers';

const selectClass = 'h-10 w-full rounded-full border border-input bg-white px-4 text-sm text-ink';

function StatusSelect({ id, value, onChange }: { id: string; value: IncidentStatus; onChange: (v: IncidentStatus) => void }) {
    return (
        <select id={id} className={selectClass} value={value} onChange={(e) => onChange(e.target.value as IncidentStatus)}>
            {INCIDENT_STATUSES.map((s) => <option key={s} value={s}>{INCIDENT_STATUS[s].label}</option>)}
        </select>
    );
}

const BODY_HINT = 'Plain text. **bold**, *italic*, `code` and "- " lists are supported; HTML is shown as text.';

function PostRow({ post, workspaceId }: { post: IncidentPost; workspaceId: number }) {
    const qc = useQueryClient();
    const key = ['trust-incidents', workspaceId];
    const [status, setStatus] = useState<IncidentStatus>(post.status);
    const [body, setBody] = useState('');
    const done = () => qc.invalidateQueries({ queryKey: key });
    const publish = useMutation({
        mutationFn: (published: boolean) => trustApi.updateIncident(post.id, { published }),
        onSuccess: done, onError: (e) => toast.error(errDetail(e)),
    });
    const add = useMutation({
        mutationFn: () => trustApi.addUpdate(post.id, { status, body }),
        onSuccess: () => { setBody(''); done(); toast.success('Update posted'); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const remove = useMutation({
        mutationFn: () => trustApi.deleteIncident(post.id),
        onSuccess: done, onError: (e) => toast.error(errDetail(e)),
    });
    const st = INCIDENT_STATUS[post.status];
    return (
        <li className="flex flex-col gap-3 border-t border-[#F0EEE8] py-4 first:border-t-0">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex min-w-0 flex-col">
                    <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-ink">
                        {post.title}
                        <Pill tone={st.tone}>{st.label}</Pill>
                        <Pill tone={post.published ? 'mint' : 'neutral'}>{post.published ? 'Public' : 'Draft'}</Pill>
                    </span>
                    <span className="text-xs text-ink-soft">
                        {post.components.length ? `${post.components.join(', ')} · ` : ''}
                        {post.updates.length} update{post.updates.length === 1 ? '' : 's'} · started {new Date(post.created_at).toLocaleString()}
                    </span>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button variant="outline" disabled={publish.isPending} onClick={() => publish.mutate(!post.published)}>
                        {post.published ? 'Unpublish' : 'Publish'}
                    </Button>
                    <Button variant="outline" disabled={remove.isPending}
                        onClick={() => { if (window.confirm(`Delete "${post.title}"? This cannot be undone.`)) remove.mutate(); }}>
                        Delete
                    </Button>
                </div>
            </div>
            {post.updates.length > 0 && (
                <ol className="flex flex-col gap-1 rounded-2xl bg-background px-4 py-3 text-sm">
                    {[...post.updates].reverse().map((u, i) => (
                        <li key={`${u.at}-${i}`} className="flex flex-col">
                            <span className="text-xs text-ink-soft">{new Date(u.at).toLocaleString()} · {INCIDENT_STATUS[u.status]?.label ?? u.status}</span>
                            <span className="whitespace-pre-wrap text-ink">{u.body}</span>
                        </li>
                    ))}
                </ol>
            )}
            <div className="flex flex-col gap-2">
                <FieldRow label="New status" htmlFor={`inc-st-${post.id}`}>
                    <StatusSelect id={`inc-st-${post.id}`} value={status} onChange={setStatus} />
                </FieldRow>
                <label htmlFor={`inc-body-${post.id}`} className="text-sm font-medium text-ink">Update</label>
                <textarea id={`inc-body-${post.id}`} rows={2} maxLength={5000} className={`${textareaClass} font-sans`} value={body} onChange={(e) => setBody(e.target.value)} />
                <p className="text-xs text-ink-soft">{BODY_HINT}</p>
                <div><Button onClick={() => add.mutate()} disabled={!body.trim() || add.isPending}>Post update</Button></div>
            </div>
        </li>
    );
}

export function TrustIncidentsCard({ workspaceId }: { workspaceId: number }) {
    const qc = useQueryClient();
    const key = ['trust-incidents', workspaceId];
    const posts = useQuery({ queryKey: key, queryFn: () => trustApi.incidents(workspaceId) });
    const [title, setTitle] = useState('');
    const [status, setStatus] = useState<IncidentStatus>('investigating');
    const [components, setComponents] = useState('');
    const [body, setBody] = useState('');
    const [published, setPublished] = useState(false);
    const create = useMutation({
        mutationFn: () => trustApi.createIncident(workspaceId, {
            title: title.trim(), status, components: parseComponents(components), body: body.trim() || undefined, published,
        }),
        onSuccess: () => {
            setTitle(''); setComponents(''); setBody(''); setPublished(false); setStatus('investigating');
            qc.invalidateQueries({ queryKey: key });
            toast.success('Incident post created');
        },
        onError: (e) => toast.error(errDetail(e)),
    });
    return (
        <SettingsCard
            labelledBy="trust-incidents"
            icon={<Megaphone className="h-4 w-4" />}
            title="Incident communications"
            description="Posts you write for customers. Only published posts appear on the public page (the newest 20)."
            footer={<Button onClick={() => create.mutate()} disabled={!title.trim() || create.isPending}>Create post</Button>}
        >
            <div className="flex flex-col">
                <FieldRow label="Title" htmlFor="inc-title" wide>
                    <Input id="inc-title" className={inputClass} maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Elevated API error rates" />
                </FieldRow>
                <FieldRow label="Status" htmlFor="inc-status">
                    <StatusSelect id="inc-status" value={status} onChange={setStatus} />
                </FieldRow>
                <FieldRow label="Affected components" htmlFor="inc-comp" hint="Comma-separated, for example API, Dashboard." wide>
                    <Input id="inc-comp" className={inputClass} value={components} onChange={(e) => setComponents(e.target.value)} />
                </FieldRow>
                <div className="flex flex-col gap-2 py-2">
                    <label htmlFor="inc-body" className="text-sm font-medium text-ink">First update</label>
                    <textarea id="inc-body" rows={3} maxLength={5000} className={`${textareaClass} font-sans`} value={body} onChange={(e) => setBody(e.target.value)} />
                    <p className="text-xs text-ink-soft">{BODY_HINT}</p>
                </div>
                <CheckRow id="inc-published" label="Publish now" hint="Leave off to save a draft." checked={published} onChange={setPublished} />
            </div>
            <div className="flex flex-col border-t border-border pt-3">
                <h3 className="mb-1 text-sm font-semibold text-ink">Posts</h3>
                {posts.isLoading ? <p className="text-sm text-ink-soft">Loading…</p>
                    : (posts.data || []).length === 0 ? <p className="text-sm text-ink-soft">No posts yet.</p>
                        : <ul className="flex flex-col">{posts.data!.map((p) => <PostRow key={`${p.id}-${p.status}`} post={p} workspaceId={workspaceId} />)}</ul>}
            </div>
        </SettingsCard>
    );
}
