import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Check, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { toast } from 'sonner';
import { getWorkspaces, Workspace } from '@/lib/api';
import { billingApi, Plan } from '@/api/billing';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';

const errDetail = (e: any): string => e?.response?.data?.detail || e?.message || 'Unknown error';
const LIMIT_LABELS: [string, string][] = [
    ['monthly_runs', 'Runs / month'], ['seats', 'Seats'], ['concurrent_runs', 'Concurrent runs'],
    ['retention_days', 'Retention (days)'], ['ai_daily', 'AI generations / day'],
    ['monthly_llm_tokens', 'LLM tokens / month'],
];
const fmtLimit = (n: number) => n === 0 ? 'Unlimited' : n.toLocaleString();

function UsageMeter({ label, used, limit }: { label: string; used: number; limit: number }) {
    const pct = limit === 0 ? 0 : Math.min(100, Math.round((used / limit) * 100));
    const tone = pct >= 90 ? 'bg-[#E0603A]' : pct >= 70 ? 'bg-[#E8B84A]' : 'bg-[#8FD3B6]';
    return (
        <div className="flex flex-col gap-2 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[26px] font-bold leading-tight tracking-tight text-ink tabular-nums">
                {used.toLocaleString()}
                <span className="ml-1.5 text-sm font-medium text-ink-soft">/ {limit === 0 ? 'unlimited' : limit.toLocaleString()}</span>
            </span>
            <div
                role="img"
                aria-label={limit === 0 ? `${label}: ${used.toLocaleString()}, no limit` : `${label}: ${pct}% of limit used`}
                className="h-2 overflow-hidden rounded-full bg-secondary"
            >
                <div className={cn('h-full rounded-full', tone)} style={{ width: limit === 0 ? '4%' : `${Math.max(2, pct)}%` }} />
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
        <div className="flex flex-col gap-5">
            <PageHeader
                title="Plan and usage"
                subtitle="Your workspace plan, metered usage for the month, and available tiers. Quotas are enforced on run creation."
                actions={
                    <Select value={wsId?.toString() ?? ''} onValueChange={(v) => setSelectedWs(parseInt(v))}>
                        <SelectTrigger aria-label="Workspace" className="h-10 w-[220px] rounded-full bg-white px-4">
                            <SelectValue placeholder="Workspace" />
                        </SelectTrigger>
                        <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                    </Select>
                }
            />

            {!wsId ? (
                <EmptyState title="No workspace available">Create or join a workspace to see its plan.</EmptyState>
            ) : status.isLoading ? (
                <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2].map((i) => <div key={i} className="h-[112px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : status.isError ? (
                <EmptyState title="Couldn't load this workspace's plan">{errDetail(status.error)}</EmptyState>
            ) : st && (
                <div className="flex flex-col gap-5">
                    <section aria-label="Current plan" className="flex flex-col gap-3">
                        <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
                            <div className="flex flex-wrap items-center gap-2">
                                <h2 className="text-[15px] font-semibold text-ink">Current plan: {st.plan.display_name}</h2>
                                <Pill tone="mint">{st.status}</Pill>
                            </div>
                            <span className="text-[13px] text-ink-soft">
                                Period {st.period}
                                {!st.stripe_configured && ' · Manual billing (Stripe not configured)'}
                            </span>
                        </div>
                        <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
                            <UsageMeter label="Runs this month" used={st.usage.runs || 0} limit={st.limits.monthly_runs ?? 0} />
                            <UsageMeter label="AI generations today" used={st.usage.ai_generations || 0} limit={st.limits.ai_daily ?? 0} />
                            <UsageMeter label="LLM tokens this month" used={st.usage.llm_tokens || 0} limit={st.limits.monthly_llm_tokens ?? 0} />
                        </div>
                    </section>

                    <section aria-label="Plans" className="flex flex-col gap-3">
                        <h2 className="px-1 text-[15px] font-semibold text-ink">Plans</h2>
                        <div className="grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
                            {(plans.data || []).map((p: Plan) => {
                                const current = p.id === st.plan.id;
                                return (
                                    <div
                                        key={p.id}
                                        className={cn(
                                            'flex flex-col gap-4 rounded-2xl border bg-white px-5 py-4',
                                            current ? 'border-ink shadow-[0_4px_16px_rgba(28,27,34,0.07)]' : 'border-border'
                                        )}
                                    >
                                        <div className="flex items-center justify-between gap-2">
                                            <h3 className="text-[15px] font-semibold text-ink">{p.display_name}</h3>
                                            {current && <Pill tone="lavender">Current</Pill>}
                                        </div>
                                        <div className="text-[28px] font-bold leading-none tracking-tight text-ink">
                                            {p.price_cents === 0 ? (p.name === 'enterprise' ? 'Custom' : 'Free') : `$${(p.price_cents / 100).toFixed(0)}`}
                                            {p.price_cents > 0 && <span className="text-sm font-normal text-ink-soft">/mo</span>}
                                        </div>
                                        <ul className="flex flex-col gap-1.5">
                                            {LIMIT_LABELS.map(([k, lbl]) => (
                                                <li key={k} className="flex items-center gap-2 text-sm text-ink-soft">
                                                    <Check className="h-3.5 w-3.5 shrink-0 text-pastel-ink-mint" aria-hidden="true" /> {lbl}: <span className="font-medium text-ink">{fmtLimit(p.limits[k] ?? 0)}</span>
                                                </li>
                                            ))}
                                        </ul>
                                        <div className="mt-auto">
                                            {current ? (
                                                <Button variant="outline" className="w-full" disabled>Current plan</Button>
                                            ) : st.stripe_configured && p.price_cents > 0 ? (
                                                <Button className="w-full" onClick={() => checkout.mutate(p.name)} disabled={checkout.isPending}>
                                                    <Zap className="h-3.5 w-3.5" /> Upgrade
                                                </Button>
                                            ) : (
                                                <Button variant="outline" className="w-full" onClick={() => assign.mutate(p.name)} disabled={assign.isPending}>
                                                    Switch to {p.display_name}
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </section>

                    <p className="px-1 text-[13px] leading-relaxed text-ink-soft">
                        Plan changes take effect immediately. {st.stripe_configured ? 'Paid upgrades go through Stripe checkout.' : 'Stripe is not configured, so a workspace admin assigns plans directly.'} Run quota is enforced when a run is created (HTTP 402 when exceeded).
                    </p>
                </div>
            )}
        </div>
    );
}
