import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, ClipboardCheck, Download, FileText, Loader2, Play, ShieldCheck, ShieldX, Signature } from 'lucide-react';
import { toast } from 'sonner';
import { errDetail } from '@/lib/errors';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { getWorkspaces } from '@/lib/api';
import { downloadFromApi, openHtmlFromApi } from '@/lib/download';
import { accessReviewApi, type ReviewCampaign, type ReviewItem, type RevocationPlan } from '@/api/securityOps';
import { accessSummary, fmtDate } from '@/components/access-review/summary';
import { useActiveWorkspaceId } from '@/hooks/useActiveWorkspace';
import { useIsInstanceAdmin } from '@/hooks/useIsInstanceAdmin';


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
        <tr className="border-t border-slate-100 align-top">
            <td className="py-2 pr-3">
                <div className="text-sm font-medium text-slate-800">{item.label}</div>
                <Badge variant="outline" className="mt-1 rounded text-[9px] uppercase">{item.subject_type === 'api_key' ? 'API key' : 'person'}</Badge>
            </td>
            <td className="py-2 pr-3 text-xs text-slate-600 space-y-0.5">
                {accessSummary(item).map((line, i) => <div key={i}>{line}</div>)}
            </td>
            <td className="py-2 pr-3 w-64">
                <Input aria-label={`Note for ${item.label}`} placeholder={locked ? '' : 'Note (required to revoke)'}
                    className="h-8 text-xs" value={note} disabled={locked || busy}
                    onChange={(e) => setNote(e.target.value)} />
                {item.decided_by && (
                    <div className="text-[10px] text-slate-400 mt-1">{item.decided_by} · {fmtDate(item.decided_at)}</div>
                )}
                {item.apply_result && <div className="text-[10px] text-slate-500 mt-0.5">Applied: {item.apply_result}</div>}
            </td>
            <td className="py-2 text-right whitespace-nowrap">
                {locked ? (
                    <Badge variant="outline" className={item.decision === 'revoke' ? 'bg-rose-50 text-rose-700 border-rose-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}>
                        {item.decision}
                    </Badge>
                ) : (
                    <div className="flex justify-end gap-1.5">
                        <Button size="sm" variant={item.decision === 'keep' ? 'default' : 'outline'} className="h-8"
                            disabled={busy} onClick={() => onDecide('keep', note)}>
                            <ShieldCheck className="w-3.5 h-3.5 mr-1" /> Keep
                        </Button>
                        <Button size="sm" variant={item.decision === 'revoke' ? 'destructive' : 'outline'} className="h-8"
                            disabled={busy || !note.trim()} title={note.trim() ? undefined : 'Add a note first'}
                            onClick={() => onDecide('revoke', note)}>
                            <ShieldX className="w-3.5 h-3.5 mr-1" /> Revoke
                        </Button>
                    </div>
                )}
            </td>
        </tr>
    );
}

function CampaignDetail({ id, onBack }: { id: number; onBack: () => void }) {
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
    if (!c) return <div className="p-10 flex justify-center"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>;
    const download = (format: 'csv' | 'html') =>
        (format === 'csv'
            ? downloadFromApi(accessReviewApi.exportPath(id, 'csv'), `access-review-${id}.csv`)
            : openHtmlFromApi(accessReviewApi.exportPath(id, 'html'))).catch((e) => toast.error(errDetail(e)));

    return (
        <div className="space-y-4">
            <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft className="w-4 h-4 mr-1" /> All reviews</Button>
            <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                <div className="flex flex-wrap items-start gap-3">
                    <div>
                        <h2 className="text-xl font-bold text-slate-900">{c.name}</h2>
                        <p className="text-xs text-slate-500 mt-1">
                            {c.scope} review · started {fmtDate(c.created_at)} by {c.created_by || 'unknown'}
                            {c.signed_off_at && <> · signed off {fmtDate(c.signed_off_at)} by {c.signed_off_by}</>}
                        </p>
                        <p className="text-xs text-slate-500 mt-0.5">
                            {c.counts.keep} keep · {c.counts.revoke} revoke · {c.counts.undecided} undecided
                            {c.counts.applied > 0 && <> · {c.counts.applied} revocation(s) applied</>}
                        </p>
                    </div>
                    <div className="ml-auto flex gap-2">
                        <Button size="sm" variant="outline" onClick={() => download('csv')}><Download className="w-3.5 h-3.5 mr-1" /> CSV</Button>
                        <Button size="sm" variant="outline" onClick={() => download('html')}><FileText className="w-3.5 h-3.5 mr-1" /> Printable report</Button>
                    </div>
                </div>

                {c.status === 'open' ? (
                    <div className="mt-4 flex flex-wrap items-center gap-2 border-t pt-4">
                        <Input aria-label="Sign-off note" placeholder="Sign-off note (optional)" className="h-9 max-w-md"
                            value={signNote} onChange={(e) => setSignNote(e.target.value)} />
                        <Button size="sm" className="h-9" disabled={signOff.isPending || c.counts.undecided > 0}
                            onClick={() => signOff.mutate()}>
                            <Signature className="w-4 h-4 mr-1" /> Sign off review
                        </Button>
                        {c.counts.undecided > 0 && <span className="text-xs text-slate-500">Decide every item first.</span>}
                    </div>
                ) : c.counts.revoke > c.counts.applied ? (
                    <div className="mt-4 border-t pt-4 space-y-2">
                        <p className="text-sm text-slate-700">
                            Revoke decisions are not applied until you confirm them here.
                        </p>
                        {!plan || plan.confirmed ? (
                            <Button size="sm" variant="outline" disabled={apply.isPending} onClick={() => apply.mutate(false)}>
                                Preview revocations
                            </Button>
                        ) : (
                            <div className="rounded-lg border border-rose-200 bg-rose-50/60 p-3 space-y-2">
                                <div className="text-sm font-medium text-rose-800">This will revoke:</div>
                                <ul className="text-xs text-rose-800 list-disc pl-5">
                                    {(plan.would_revoke || []).map((p) => <li key={p.item_id}>{p.label} — {p.note}</li>)}
                                </ul>
                                <div className="flex gap-2">
                                    <Button size="sm" variant="destructive" disabled={apply.isPending} onClick={() => apply.mutate(true)}>
                                        Confirm and revoke
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={() => setPlan(null)}>Cancel</Button>
                                </div>
                            </div>
                        )}
                    </div>
                ) : null}
            </div>

            <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm overflow-x-auto">
                <table className="w-full text-left">
                    <thead>
                        <tr className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                            <th className="pb-2 pr-3">Who / what</th>
                            <th className="pb-2 pr-3">Access at review start</th>
                            <th className="pb-2 pr-3">Note</th>
                            <th className="pb-2 text-right">Decision</th>
                        </tr>
                    </thead>
                    <tbody>
                        {(c.items || []).map((item) => (
                            <ItemRow key={`${item.id}:${item.decided_at ?? ''}`} item={item} campaign={c} busy={decide.isPending}
                                onDecide={(decision, note) => decide.mutate({ itemId: item.id, decision, note })} />
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
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

    return (
        <div className="max-w-[1200px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <h1 className="text-4xl font-extrabold tracking-tight text-slate-900 flex items-center gap-3">
                    <ClipboardCheck className="w-8 h-8 text-indigo-500" /> Access Reviews
                </h1>
                <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                    Who can do what — every person's workspace, team and project roles, how they sign in, and every
                    live API key. Run a review, mark each grant keep or revoke, and sign it off; the sign-off is
                    recorded in the audit trail. Revocations apply only when you confirm them.
                </p>
            </div>

            {openId ? <CampaignDetail id={openId} onBack={() => setOpenId(null)} /> : (
                <div className="space-y-6">
                    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm flex flex-wrap items-end gap-3">
                        <label className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                            Scope
                            <select aria-label="Review scope" className="block mt-1 h-9 rounded-lg border border-slate-200 bg-white px-2 text-sm text-slate-700 normal-case tracking-normal font-normal"
                                value={scope} onChange={(e) => setScope(e.target.value)}>
                                {(workspaces.data || []).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                                {isInstanceAdmin && <option value={INSTANCE}>Whole instance</option>}
                            </select>
                        </label>
                        <Button variant="outline" size="sm" className="h-9" onClick={() => report('csv')} disabled={workspaceId === undefined}>
                            <Download className="w-3.5 h-3.5 mr-1" /> Current access (CSV)
                        </Button>
                        <Button variant="outline" size="sm" className="h-9" onClick={() => report('html')} disabled={workspaceId === undefined}>
                            <FileText className="w-3.5 h-3.5 mr-1" /> Printable report
                        </Button>
                        <div className="ml-auto flex gap-2">
                            <Input aria-label="Review name" placeholder="Review name (optional)" className="h-9 w-60" value={name} onChange={(e) => setName(e.target.value)} />
                            <Button size="sm" className="h-9" disabled={start.isPending || workspaceId === undefined} onClick={() => start.mutate()}>
                                <Play className="w-3.5 h-3.5 mr-1" /> Start review
                            </Button>
                        </div>
                    </div>

                    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                        <h3 className="font-bold text-slate-800 mb-3">Reviews</h3>
                        {campaigns.isLoading ? <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                            : campaigns.error ? <p className="text-sm text-rose-600">{errDetail(campaigns.error)}</p>
                                : !campaigns.data?.length ? <p className="text-sm text-slate-400">No reviews yet for this scope.</p>
                                    : (
                                        <div className="space-y-2">
                                            {campaigns.data.map((c) => (
                                                <button key={c.id} onClick={() => setOpenId(c.id)}
                                                    className="w-full text-left border border-slate-200 rounded-xl p-3 hover:bg-slate-50 flex items-center gap-3">
                                                    <div className="min-w-0">
                                                        <div className="font-semibold text-slate-800 text-sm">{c.name}</div>
                                                        <div className="text-xs text-slate-500">
                                                            started {fmtDate(c.created_at)} by {c.created_by || 'unknown'} ·
                                                            {' '}{c.counts.total} items, {c.counts.undecided} undecided, {c.counts.revoke} revoke
                                                        </div>
                                                    </div>
                                                    <Badge variant="outline" className={`ml-auto ${c.status === 'open' ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}>
                                                        {c.status === 'open' ? 'in review' : 'signed off'}
                                                    </Badge>
                                                </button>
                                            ))}
                                        </div>
                                    )}
                    </div>
                </div>
            )}
        </div>
    );
}
