import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    RefreshCw, AlertCircle, Building2, Check, Zap, Info,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, Workspace } from '@/lib/api';
import { billingApi, Plan } from '@/api/billing';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const LIMIT_LABELS: [string, string][] = [
    ['monthly_runs', 'Runs / month'], ['seats', 'Seats'], ['concurrent_runs', 'Concurrent runs'],
    ['retention_days', 'Retention (days)'], ['ai_daily', 'AI generations / day'],
];
const fmtLimit = (n: number) => n === 0 ? 'Unlimited' : n.toLocaleString();

function UsageMeter({ label, used, limit }: { label: string; used: number; limit: number }) {
    const pct = limit === 0 ? 0 : Math.min(100, Math.round((used / limit) * 100));
    const tone = pct >= 90 ? 'bg-rose-500' : pct >= 70 ? 'bg-amber-500' : 'bg-emerald-500';
    return (
        <div>
            <div className="flex justify-between text-sm mb-1">
                <span className="text-slate-600">{label}</span>
                <span className="tabular-nums text-slate-500">{used.toLocaleString()} / {limit === 0 ? '∞' : limit.toLocaleString()}</span>
            </div>
            <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                <div className={`h-full rounded-full ${tone}`} style={{ width: limit === 0 ? '4%' : `${Math.max(2, pct)}%` }} />
            </div>
        </div>
    );
}

export default function Billing() {
    const qc = useQueryClient();
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const wsId = selectedWs ?? workspaces?.[0]?.id ?? null;

    const status = useQuery({ queryKey: ['billing', wsId], queryFn: () => billingApi.status(wsId!), enabled: !!wsId });
    const plans = useQuery({ queryKey: ['plans'], queryFn: () => billingApi.plans() });

    const assign = useMutation({
        mutationFn: (planName: string) => billingApi.assignPlan(wsId!, planName),
        onSuccess: () => { toast.success('Plan updated'); qc.invalidateQueries({ queryKey: ['billing', wsId] }); },
        onError: (e) => toast.error(`${errDetail(e)}`),
    });
    const checkout = useMutation({
        mutationFn: (planName: string) => billingApi.checkout(wsId!, planName),
        onSuccess: (d) => { window.location.href = d.checkout_url; },
        onError: (e) => toast.error(errDetail(e)),
    });

    const st = status.data;

    return (
        <div className="max-w-[1100px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Billing</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Plan &amp; Usage</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Your workspace plan, metered usage for the month, and available tiers. Quotas are
                            enforced on run creation.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0">
                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Workspace</p>
                        <Select value={wsId?.toString() ?? ''} onValueChange={(v) => setSelectedWs(parseInt(v))}>
                            <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                                <div className="flex items-center gap-2 min-w-0"><Building2 className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Workspace" /></div>
                            </SelectTrigger>
                            <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
            </div>

            {!wsId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">No workspace available.</p></div>
            ) : status.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : st && (
                <div className="space-y-6">
                    <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                        <div className="flex items-center justify-between mb-5">
                            <div>
                                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Current plan</div>
                                <div className="text-2xl font-extrabold text-slate-900 flex items-center gap-2">
                                    {st.plan.display_name}
                                    <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-emerald-50 text-emerald-700 border-emerald-200">{st.status}</Badge>
                                </div>
                            </div>
                            <div className="text-right text-sm text-slate-400">
                                <div>Period {st.period}</div>
                                {!st.stripe_configured && <div className="text-[11px] mt-1">Manual billing (Stripe not configured)</div>}
                            </div>
                        </div>
                        <div className="grid sm:grid-cols-2 gap-4 max-w-2xl">
                            <UsageMeter label="Runs this month" used={st.usage.runs || 0} limit={st.limits.monthly_runs ?? 0} />
                            <UsageMeter label="AI generations today" used={st.usage.ai_generations || 0} limit={st.limits.ai_daily ?? 0} />
                        </div>
                    </div>

                    <div className="grid md:grid-cols-3 gap-4">
                        {(plans.data || []).map((p: Plan) => {
                            const current = p.id === st.plan.id;
                            return (
                                <div key={p.id} className={`bg-white border rounded-2xl p-5 shadow-sm ${current ? 'border-indigo-300 ring-1 ring-indigo-200' : 'border-slate-200'}`}>
                                    <div className="flex items-center justify-between mb-1">
                                        <h3 className="font-bold text-slate-800 text-lg">{p.display_name}</h3>
                                        {current && <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-indigo-50 text-indigo-700 border-indigo-200">current</Badge>}
                                    </div>
                                    <div className="text-2xl font-extrabold text-slate-900 mb-3">
                                        {p.price_cents === 0 ? (p.name === 'enterprise' ? 'Custom' : 'Free') : `$${(p.price_cents / 100).toFixed(0)}`}
                                        {p.price_cents > 0 && <span className="text-sm font-normal text-slate-400">/mo</span>}
                                    </div>
                                    <ul className="space-y-1.5 mb-4">
                                        {LIMIT_LABELS.map(([k, lbl]) => (
                                            <li key={k} className="flex items-center gap-2 text-sm text-slate-600">
                                                <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0" /> {lbl}: <span className="font-medium">{fmtLimit(p.limits[k] ?? 0)}</span>
                                            </li>
                                        ))}
                                    </ul>
                                    {current ? (
                                        <Button size="sm" variant="outline" className="w-full h-9 rounded-lg" disabled>Current plan</Button>
                                    ) : st.stripe_configured && p.price_cents > 0 ? (
                                        <Button size="sm" className="w-full h-9 rounded-lg" onClick={() => checkout.mutate(p.name)} disabled={checkout.isPending}>
                                            <Zap className="w-3.5 h-3.5 mr-1.5" /> Upgrade
                                        </Button>
                                    ) : (
                                        <Button size="sm" variant="outline" className="w-full h-9 rounded-lg" onClick={() => assign.mutate(p.name)} disabled={assign.isPending}>
                                            Switch to {p.display_name}
                                        </Button>
                                    )}
                                </div>
                            );
                        })}
                    </div>

                    <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5">
                        <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
                        <p>Plan changes take effect immediately. {st.stripe_configured ? 'Paid upgrades go through Stripe checkout.' : 'Stripe is not configured, so a workspace admin assigns plans directly.'} Run quota is enforced when a run is created (HTTP 402 when exceeded).</p>
                    </div>
                </div>
            )}
        </div>
    );
}
