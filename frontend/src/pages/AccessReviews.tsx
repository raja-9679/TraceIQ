import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, FileText, Loader2, Play, ShieldCheck, ShieldX, Signature } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getWorkspaces } from '@/lib/api';
import { downloadFromApi, openHtmlFromApi } from '@/lib/download';
import { accessReviewApi, type ReviewCampaign, type ReviewItem, type RevocationPlan } from '@/api/securityOps';
import { accessSummary, fmtDate } from '@/components/access-review/summary';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';
import { useIsInstanceAdmin } from '@/hooks/useIsInstanceAdmin';
import {
    DetailCard, EmptyState, Eyebrow, ListGroup, ListRow, PageHeader, Pill, SplitView,
} from '@/components/redesign/primitives';
import { dangerOutline, fieldLabel, nativeSelect } from '@/components/redesign-local/ops/look';


const INSTANCE = 'instance';

function ItemRow({ item, campaign, onDecide, busy }: {
    item: ReviewItem;
    campaign: ReviewCampaign;
    onDecide: (decision: 'keep' | 'revoke', note: string) => void;
    busy: boolean;
}) {
    // Initialised once per decision: the parent keys rows on decided_at, so a
    // saved decision remounts the row with the stored note.
    const [note, setNote] = useState(item.note || '');
    const locked = campaign.status !== 'open';
    return (
        <li className="flex flex-col gap-3 px-4 py-4 sm:px-5">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="flex min-w-0 flex-col gap-1">
                    <span className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold text-ink [overflow-wrap:anywhere]">{item.label}</span>
                        <Pill tone="neutral">{item.subject_type === 'api_key' ? 'API key' : 'Person'}</Pill>
                    </span>
                    <div className="flex flex-col gap-0.5 text-xs leading-relaxed text-ink-soft">
                        {accessSummary(item).map((line, i) => <div key={i}>{line}</div>)}
                    </div>
                </div>
                {locked && (
                    <Pill tone={item.decision === 'revoke' ? 'peach' : 'mint'} className="capitalize">{item.decision}</Pill>
                )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <Input aria-label={`Note for ${item.label}`} placeholder={locked ? '' : 'Note (required to revoke)'}
                    className="h-9 min-w-[200px] flex-1 text-sm" value={note} disabled={locked || busy}
                    onChange={(e) => setNote(e.target.value)} />
                {!locked && (
                    <>
                        <Button size="sm" variant={item.decision === 'keep' ? 'default' : 'outline'}
                            disabled={busy} onClick={() => onDecide('keep', note)}>
                            <ShieldCheck /> Keep
                        </Button>
                        <Button size="sm" variant="outline"
                            className={item.decision === 'revoke' ? 'border-pastel-ink-peach bg-pastel-peach text-pastel-ink-peach hover:bg-pastel-peach' : dangerOutline}
                            disabled={busy || !note.trim()} title={note.trim() ? undefined : 'Add a note first'}
                            onClick={() => onDecide('revoke', note)}>
                            <ShieldX /> Revoke
                        </Button>
                    </>
                )}
            </div>
            {(item.decided_by || item.apply_result) && (
                <div className="flex flex-col gap-0.5 text-[11px] text-ink-soft">
                    {item.decided_by && <span>{item.decided_by} · {fmtDate(item.decided_at)}</span>}
                    {item.apply_result && <span>Applied: {item.apply_result}</span>}
                </div>
            )}
        </li>
    );
}

function CampaignDetail({ id }: { id: number }) {
    const qc = useQueryClient();
    const campaign = useQuery({ queryKey: ['access-review', id], queryFn: () => accessReviewApi.get(id) });
    const [signNote, setSignNote] = useState('');
    const [plan, setPlan] = useState<RevocationPlan | null>(null);
    const refresh = () => qc.invalidateQueries({ queryKey: ['access-review', id] });

    const decide = useMutation({
        mutationFn: ({ itemId, decision, note }: { itemId: number; decision: 'keep' | 'revoke'; note: string }) =>
            accessReviewApi.decide(id, itemId, decision, note),
        onSuccess: refresh,
        onError: (e) => toast.error(errDetail(e)),
    });
    const signOff = useMutation({
        mutationFn: () => accessReviewApi.signOff(id, signNote),
        onSuccess: () => { toast.success('Review signed off — recorded in the audit trail'); refresh(); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const apply = useMutation({
        mutationFn: (confirm: boolean) => accessReviewApi.applyRevocations(id, confirm),
        onSuccess: (res) => {
            setPlan(res);
            if (res.confirmed) { toast.success(`Applied ${res.results?.length ?? 0} revocation(s)`); refresh(); }
        },
        onError: (e) => toast.error(errDetail(e)),
    });

    const c = campaign.data;
    if (!c) {
        return (
            <DetailCard label="Loading review">
                <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-ink-soft" /></div>
            </DetailCard>
        );
    }
    const download = (format: 'csv' | 'html') =>
        (format === 'csv'
            ? downloadFromApi(accessReviewApi.exportPath(id, 'csv'), `access-review-${id}.csv`)
            : openHtmlFromApi(accessReviewApi.exportPath(id, 'html'))).catch((e) => toast.error(errDetail(e)));

    return (
        <DetailCard label={`Access review ${c.name}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <Pill tone={c.status === 'open' ? 'butter' : 'mint'}>{c.status === 'open' ? 'In review' : 'Signed off'}</Pill>
                        <span className="text-[13px] capitalize text-ink-soft">{c.scope} review</span>
                    </div>
                    <h2 className="break-words text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{c.name}</h2>
                    <p className="text-[13px] text-ink-soft">
                        Started {fmtDate(c.created_at)} by {c.created_by || 'unknown'}
                        {c.signed_off_at && <> · signed off {fmtDate(c.signed_off_at)} by {c.signed_off_by}</>}
                    </p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => download('csv')}><Download /> CSV</Button>
                    <Button size="sm" variant="outline" onClick={() => download('html')}><FileText /> Printable report</Button>
                </div>
            </div>

            <div className="flex flex-wrap gap-1.5">
                <Pill tone="mint">{c.counts.keep} keep</Pill>
                <Pill tone="peach">{c.counts.revoke} revoke</Pill>
                <Pill tone="neutral">{c.counts.undecided} undecided</Pill>
                {c.counts.applied > 0 && <Pill tone="lavender">{c.counts.applied} revocation(s) applied</Pill>}
            </div>

            {c.status === 'open' ? (
                <div className="flex flex-col gap-2 rounded-2xl bg-background p-4">
                    <Eyebrow>Sign off</Eyebrow>
                    <div className="flex flex-wrap items-center gap-2">
                        <Input aria-label="Sign-off note" placeholder="Sign-off note (optional)" className="h-10 min-w-[200px] flex-1 bg-white"
                            value={signNote} onChange={(e) => setSignNote(e.target.value)} />
                        <Button disabled={signOff.isPending || c.counts.undecided > 0} onClick={() => signOff.mutate()}>
                            <Signature /> Sign off review
                        </Button>
                    </div>
                    {c.counts.undecided > 0 && <span className="text-xs text-ink-soft">Decide every item first.</span>}
                </div>
            ) : c.counts.revoke > c.counts.applied ? (
                <div className="flex flex-col gap-3 rounded-2xl bg-background p-4">
                    <Eyebrow>Revocations</Eyebrow>
                    <p className="text-sm text-ink">Revoke decisions are not applied until you confirm them here.</p>
                    {!plan || plan.confirmed ? (
                        <Button variant="outline" className="self-start bg-white" disabled={apply.isPending} onClick={() => apply.mutate(false)}>
                            Preview revocations
                        </Button>
                    ) : (
                        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-white p-4 shadow-[inset_4px_0_0_#E0603A]">
                            <div className="text-sm font-semibold text-ink">This will revoke:</div>
                            <ul className="list-disc pl-5 text-[13px] text-ink">
                                {(plan.would_revoke || []).map((p) => <li key={p.item_id}>{p.label} — {p.note}</li>)}
                            </ul>
                            <div className="flex flex-wrap gap-2">
                                <Button variant="destructive" disabled={apply.isPending} onClick={() => apply.mutate(true)}>
                                    Confirm and revoke
                                </Button>
                                <Button variant="outline" onClick={() => setPlan(null)}>Cancel</Button>
                            </div>
                        </div>
                    )}
                </div>
            ) : null}

            <div className="flex flex-col gap-2">
                <Eyebrow right={<span>{(c.items || []).length} items</span>}>Access at review start</Eyebrow>
                {(c.items || []).length === 0 ? (
                    <p className="text-sm text-ink-soft">Nothing to review in this scope.</p>
                ) : (
                    <ul className="divide-y divide-[#F0EEE8] overflow-hidden rounded-2xl border border-border">
                        {(c.items || []).map((item) => (
                            <ItemRow key={`${item.id}:${item.decided_at ?? ''}`} item={item} campaign={c} busy={decide.isPending}
                                onDecide={(decision, note) => decide.mutate({ itemId: item.id, decision, note })} />
                        ))}
                    </ul>
                )}
            </div>
        </DetailCard>
    );
}

export default function AccessReviews() {
    const qc = useQueryClient();
    const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const [chosenScope, setScope] = useState<string>('');
    const [name, setName] = useState('');
    const [openId, setOpenId] = useState<number | null>(null);
    // Default to the sidebar project's workspace (else the first one) until
    // the user picks a scope.
    const activeWs = useActiveWorkspaceId();
    // Instance scope is offered to instance admins only (see PolicyCards).
    const isInstanceAdmin = useIsInstanceAdmin();
    const scope = chosenScope || (activeWs ? String(activeWs) : workspaces.data?.length ? String(workspaces.data[0].id) : '');
    const workspaceId = scope === INSTANCE ? null : scope ? Number(scope) : undefined;

    const campaigns = useQuery({
        queryKey: ['access-reviews', scope],
        queryFn: () => accessReviewApi.list(workspaceId as number | null),
        enabled: workspaceId !== undefined,
        retry: false,
    });
    const start = useMutation({
        mutationFn: () => accessReviewApi.start(workspaceId as number | null, name),
        onSuccess: (c) => { toast.success('Review started'); setName(''); qc.invalidateQueries({ queryKey: ['access-reviews', scope] }); setOpenId(c.id); },
        onError: (e) => toast.error(errDetail(e)),
    });
    const report = (format: 'csv' | 'html') => {
        if (workspaceId === undefined) return;
        const path = accessReviewApi.reportPath(workspaceId, format);
        (format === 'csv' ? downloadFromApi(path, 'access-report.csv') : openHtmlFromApi(path))
            .catch((e) => toast.error(errDetail(e)));
    };

    const list = campaigns.data ?? [];
    const selectedId = openId ?? list[0]?.id ?? null;
    const groups = [
        { name: 'In review', items: list.filter((c) => c.status === 'open') },
        { name: 'Signed off', items: list.filter((c) => c.status !== 'open') },
    ].filter((g) => g.items.length > 0);

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Access reviews"
                subtitle={<>
                    Who can do what — every person's workspace, team and project roles, how they sign in, and every
                    live API key. Run a review, mark each grant keep or revoke, and sign it off; the sign-off is
                    recorded in the audit trail. Revocations apply only when you confirm them.
                </>}
                toolbar={
                    <>
                        <label className="flex items-center gap-2">
                            <span className={fieldLabel}>Scope</span>
                            <select aria-label="Review scope" className={nativeSelect}
                                value={scope} onChange={(e) => { setScope(e.target.value); setOpenId(null); }}>
                                {(workspaces.data || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                                {isInstanceAdmin && <option value={INSTANCE}>Whole instance</option>}
                            </select>
                        </label>
                        <Button variant="outline" size="sm" className="h-10" onClick={() => report('csv')} disabled={workspaceId === undefined}>
                            <Download /> Current access (CSV)
                        </Button>
                        <Button variant="outline" size="sm" className="h-10" onClick={() => report('html')} disabled={workspaceId === undefined}>
                            <FileText /> Printable report
                        </Button>
                    </>
                }
            />

            <section aria-label="Start a review" className="flex flex-wrap items-end gap-3 rounded-3xl border border-border bg-white px-5 py-4 sm:px-6">
                <div className="flex min-w-[220px] flex-1 flex-col gap-1.5">
                    <label htmlFor="review-name" className={fieldLabel}>New review</label>
                    <Input id="review-name" aria-label="Review name" placeholder="Review name (optional)" className="h-10"
                        value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <Button disabled={start.isPending || workspaceId === undefined} onClick={() => start.mutate()}>
                    <Play /> Start review
                </Button>
            </section>

            {campaigns.isLoading ? (
                <div className="h-40 animate-pulse rounded-3xl bg-white/70" aria-busy="true" aria-label="Loading" />
            ) : campaigns.error ? (
                <EmptyState title="Couldn't load reviews">{errDetail(campaigns.error)}</EmptyState>
            ) : !list.length && selectedId === null ? (
                <EmptyState title="No reviews yet">No reviews yet for this scope. Start one above.</EmptyState>
            ) : (
                <SplitView
                    list={groups.map((g) => (
                        <ListGroup key={g.name} title={g.name} count={g.items.length}>
                            {g.items.map((c) => (
                                <ListRow
                                    key={c.id}
                                    selected={selectedId === c.id}
                                    onSelect={() => setOpenId(c.id)}
                                    title={c.name}
                                    meta={<span>
                                        started {fmtDate(c.created_at)} by {c.created_by || 'unknown'} ·
                                        {' '}{c.counts.total} items, {c.counts.undecided} undecided, {c.counts.revoke} revoke
                                    </span>}
                                    trailing={<Pill tone={c.status === 'open' ? 'butter' : 'mint'}>{c.status === 'open' ? 'in review' : 'signed off'}</Pill>}
                                />
                            ))}
                        </ListGroup>
                    ))}
                    detail={selectedId !== null && <CampaignDetail key={selectedId} id={selectedId} />}
                />
            )}
        </div>
    );
}
