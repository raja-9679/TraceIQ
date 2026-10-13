import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getWorkspaces, Workspace } from '@/lib/api';
import { llmUsageApi, LLMUsageSummary } from '@/api/llmUsage';
import { EmptyState, PageHeader, Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';

const fmtTokens = (n: number) => {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}k`;
    return n.toLocaleString();
};
const fmtMs = (ms: number) => ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`;
const FEATURE_LABELS: Record<string, string> = {
    failure_analysis: 'Failure analysis',
    selector_heal: 'Selector healing',
    case_generation: 'Test generation',
    unknown: 'Other',
};

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
    return (
        <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
            <span className="text-[13px] text-ink-soft">{label}</span>
            <span className="text-[28px] font-bold leading-tight tracking-tight text-ink tabular-nums">{value}</span>
            {sub && <span className="text-xs text-ink-soft">{sub}</span>}
        </div>
    );
}

function Card({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section aria-label={title} className="flex flex-col gap-4 rounded-2xl border border-border bg-white px-5 py-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
                {right}
            </div>
            {children}
        </section>
    );
}

function DailyTrend({ daily }: { daily: LLMUsageSummary['daily'] }) {
    const max = Math.max(...daily.map((d) => d.total_tokens), 1);
    const total = daily.reduce((s, d) => s + d.total_tokens, 0);
    return (
        <div>
            <div className="mb-1 flex justify-between text-xs text-ink-soft tabular-nums">
                <span>0</span>
                <span>peak {fmtTokens(max)} tokens/day</span>
            </div>
            <div
                role="img"
                aria-label={`Tokens per day from ${daily[0]?.date} to ${daily[daily.length - 1]?.date}: ${total.toLocaleString()} in total, peak ${max.toLocaleString()} in one day`}
                className="flex h-36 items-end gap-[2px] border-b border-border"
            >
                {daily.map((d) => (
                    <div key={d.date} className="group relative flex h-full max-w-6 flex-1 items-end">
                        <div
                            className="w-full rounded-t-[4px] bg-ink/75 transition-colors group-hover:bg-ink"
                            style={{ height: `${Math.max(2, (d.total_tokens / max) * 100)}%` }}
                        />
                        <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 hidden -translate-x-1/2 whitespace-nowrap rounded-lg bg-ink px-2.5 py-1.5 text-[11px] text-white shadow-lg group-hover:block">
                            <span className="font-semibold">{d.date}</span> · {d.total_tokens.toLocaleString()} tokens · {d.calls} call{d.calls === 1 ? '' : 's'}
                        </div>
                    </div>
                ))}
            </div>
            <div className="mt-1 flex justify-between text-xs text-ink-soft">
                <span>{daily[0]?.date}</span>
                <span>{daily[daily.length - 1]?.date}</span>
            </div>
        </div>
    );
}

export default function LLMUsage() {
    const [selectedWs, setSelectedWs] = useState<number | null>(null);
    const [days, setDays] = useState(30);
    const { data: workspaces } = useQuery<Workspace[]>({ queryKey: ['workspaces'], queryFn: getWorkspaces });
    const wsId = selectedWs ?? workspaces?.[0]?.id ?? null;

    const usage = useQuery({
        queryKey: ['llm-usage', wsId, days],
        queryFn: () => llmUsageApi.summary(wsId!, days),
        enabled: !!wsId,
    });
    const u = usage.data;
    const quotaPct = u && u.period_tokens_limit > 0
        ? Math.min(100, Math.round((u.period_tokens_used / u.period_tokens_limit) * 100)) : 0;

    return (
        <div className="flex flex-col gap-5">
            <PageHeader
                title="AI usage"
                subtitle="Every AI call TraceIQ makes on your behalf (failure analysis, selector healing, test generation), broken down by provider, model and feature."
                actions={
                    <>
                        <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                            <SelectTrigger aria-label="Time window" className="h-10 w-[120px] rounded-full bg-white px-4"><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="7">7 days</SelectItem>
                                <SelectItem value="30">30 days</SelectItem>
                                <SelectItem value="90">90 days</SelectItem>
                            </SelectContent>
                        </Select>
                        <Select value={wsId?.toString() ?? ''} onValueChange={(v) => setSelectedWs(parseInt(v))}>
                            <SelectTrigger aria-label="Workspace" className="h-10 w-[220px] rounded-full bg-white px-4">
                                <SelectValue placeholder="Workspace" />
                            </SelectTrigger>
                            <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                        </Select>
                    </>
                }
            />

            {!wsId ? (
                <EmptyState title="No workspace available">Create or join a workspace to see its AI usage.</EmptyState>
            ) : usage.isLoading ? (
                <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3" aria-busy="true" aria-label="Loading">
                    {[0, 1, 2, 3].map((i) => <div key={i} className="h-[92px] animate-pulse rounded-2xl bg-white/70" />)}
                </div>
            ) : u && u.totals.calls === 0 ? (
                <EmptyState title={`No LLM calls in the last ${days} days`}>
                    Usage appears here once AI failure analysis, selector healing, or test generation runs with an LLM provider configured.
                </EmptyState>
            ) : u && (
                <div className="flex flex-col gap-5">
                    <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
                        <Stat label="Total tokens" value={fmtTokens(u.totals.total_tokens)} sub={`${u.totals.total_tokens.toLocaleString()} in ${days}d`} />
                        <Stat label="LLM calls" value={u.totals.calls.toLocaleString()} />
                        <Stat label="Input / output" value={<span>{fmtTokens(u.totals.input_tokens)} <span className="text-ink-muted">/</span> {fmtTokens(u.totals.output_tokens)}</span>} />
                        <Stat label="Average latency" value={fmtMs(u.totals.avg_latency_ms)} />
                    </div>

                    <Card
                        title="Tokens per day"
                        right={u.period_tokens_limit > 0 && (
                            <span className="text-xs text-ink-soft tabular-nums">
                                {u.period}: {u.period_tokens_used.toLocaleString()} / {u.period_tokens_limit.toLocaleString()} tokens ({quotaPct}%)
                            </span>
                        )}
                    >
                        {u.daily.length === 0
                            ? <p className="text-sm text-ink-soft">No calls in this window.</p>
                            : <DailyTrend daily={u.daily} />}
                    </Card>

                    <Card title="By provider and model">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Provider</TableHead>
                                    <TableHead>Model</TableHead>
                                    <TableHead className="text-right">Calls</TableHead>
                                    <TableHead className="text-right">Input</TableHead>
                                    <TableHead className="text-right">Output</TableHead>
                                    <TableHead className="text-right">Total tokens</TableHead>
                                    <TableHead className="text-right">Avg latency</TableHead>
                                    <TableHead className="text-right">Success</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {u.by_provider.map((p) => (
                                    <TableRow key={`${p.provider}:${p.model}`}>
                                        <TableCell><Pill tone="lavender">{p.provider}</Pill></TableCell>
                                        <TableCell className="font-mono text-xs text-ink-soft">{p.model || '—'}</TableCell>
                                        <TableCell className="text-right tabular-nums text-ink">{p.calls.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-ink-soft">{p.input_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-ink-soft">{p.output_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right font-semibold tabular-nums text-ink">{p.total_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-ink-soft">{fmtMs(p.avg_latency_ms)}</TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            <span className={cn(p.success_rate < 0.9 ? 'font-semibold text-pastel-ink-peach' : 'text-ink-soft')}>{Math.round(p.success_rate * 100)}%</span>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </Card>

                    <Card title="By feature">
                        <div className="flex max-w-2xl flex-col gap-3">
                            {u.by_feature.map((f) => {
                                const share = u.totals.total_tokens ? (f.total_tokens / u.totals.total_tokens) * 100 : 0;
                                return (
                                    <div key={f.feature}>
                                        <div className="mb-1 flex flex-wrap justify-between gap-x-3 text-sm">
                                            <span className="text-ink">{FEATURE_LABELS[f.feature] || f.feature}</span>
                                            <span className="tabular-nums text-ink-soft">{f.total_tokens.toLocaleString()} tokens · {f.calls} calls</span>
                                        </div>
                                        <div className="h-2 overflow-hidden rounded-full bg-pastel-lavender" aria-hidden="true">
                                            <div className="h-full rounded-full bg-ink/75" style={{ width: `${Math.max(2, share)}%` }} />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </Card>
                </div>
            )}
        </div>
    );
}
