import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    RefreshCw, AlertCircle, Building2, Sparkles, Cpu, ArrowDownToLine, TimerReset,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { getWorkspaces, Workspace } from '@/lib/api';
import { llmUsageApi, LLMUsageSummary } from '@/api/llmUsage';

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

function Stat({ label, value, sub, icon: Icon }: { label: string; value: React.ReactNode; sub?: string; icon: any }) {
    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</span>
                <Icon className="w-4 h-4 text-indigo-500" />
            </div>
            <div className="text-3xl font-extrabold tabular-nums text-slate-800">{value}</div>
            {sub && <div className="text-xs text-slate-400 mt-1">{sub}</div>}
        </div>
    );
}

function DailyTrend({ daily }: { daily: LLMUsageSummary['daily'] }) {
    const max = Math.max(...daily.map((d) => d.total_tokens), 1);
    return (
        <div>
            <div className="flex justify-between text-[10px] text-slate-400 mb-1 tabular-nums">
                <span>0</span>
                <span>peak {fmtTokens(max)} tokens/day</span>
            </div>
            <div className="flex items-end gap-[2px] h-36 border-b border-slate-200">
                {daily.map((d) => (
                    <div key={d.date} className="group relative flex-1 max-w-6 flex items-end h-full">
                        <div
                            className="w-full bg-indigo-500 rounded-t group-hover:bg-indigo-600 transition-colors"
                            style={{ height: `${Math.max(2, (d.total_tokens / max) * 100)}%` }}
                        />
                        <div className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 hidden group-hover:block z-10 whitespace-nowrap bg-slate-800 text-white text-[11px] rounded-lg px-2.5 py-1.5 shadow-lg">
                            <span className="font-semibold">{d.date}</span> · {d.total_tokens.toLocaleString()} tokens · {d.calls} call{d.calls === 1 ? '' : 's'}
                        </div>
                    </div>
                ))}
            </div>
            <div className="flex justify-between text-[10px] text-slate-400 mt-1">
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
        <div className="max-w-[1100px] mx-auto pb-16">
            <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
                <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
                    <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">AI Usage</span>
                </div>
                <div className="flex flex-col sm:flex-row sm:items-end gap-4">
                    <div>
                        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">LLM Token Usage</h1>
                        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
                            Every AI call TraceIQ makes on your behalf — failure analysis, selector healing,
                            test generation — broken down by provider, model, and feature.
                        </p>
                    </div>
                    <div className="sm:ml-auto shrink-0 flex gap-3">
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Window</p>
                            <Select value={days.toString()} onValueChange={(v) => setDays(parseInt(v))}>
                                <SelectTrigger className="w-[110px] h-10 rounded-xl bg-white border-slate-200"><SelectValue /></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="7">7 days</SelectItem>
                                    <SelectItem value="30">30 days</SelectItem>
                                    <SelectItem value="90">90 days</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div>
                            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Workspace</p>
                            <Select value={wsId?.toString() ?? ''} onValueChange={(v) => setSelectedWs(parseInt(v))}>
                                <SelectTrigger className="w-[220px] h-10 rounded-xl bg-white border-slate-200">
                                    <div className="flex items-center gap-2 min-w-0"><Building2 className="w-4 h-4 text-indigo-500 shrink-0" /><SelectValue placeholder="Workspace" /></div>
                                </SelectTrigger>
                                <SelectContent>{(workspaces || []).map((w) => <SelectItem key={w.id} value={w.id.toString()}>{w.name}</SelectItem>)}</SelectContent>
                            </Select>
                        </div>
                    </div>
                </div>
            </div>

            {!wsId ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl"><AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" /><p className="text-slate-500 text-sm">No workspace available.</p></div>
            ) : usage.isLoading ? (
                <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
            ) : u && u.totals.calls === 0 ? (
                <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
                    <Sparkles className="w-10 h-10 text-slate-300 mx-auto mb-4" />
                    <p className="text-slate-600 font-semibold mb-1">No LLM calls in the last {days} days</p>
                    <p className="text-slate-400 text-sm max-w-md mx-auto">Usage appears here once AI failure analysis, selector healing, or test generation runs with an LLM provider configured.</p>
                </div>
            ) : u && (
                <div className="space-y-6">
                    <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4">
                        <Stat label="Total tokens" value={fmtTokens(u.totals.total_tokens)} sub={`${u.totals.total_tokens.toLocaleString()} in ${days}d`} icon={Sparkles} />
                        <Stat label="LLM calls" value={u.totals.calls.toLocaleString()} icon={Cpu} />
                        <Stat label="Input / output" value={<span>{fmtTokens(u.totals.input_tokens)} <span className="text-slate-300">/</span> {fmtTokens(u.totals.output_tokens)}</span>} icon={ArrowDownToLine} />
                        <Stat label="Avg latency" value={fmtMs(u.totals.avg_latency_ms)} icon={TimerReset} />
                    </div>

                    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="font-bold text-slate-800">Tokens per day</h3>
                            {u.period_tokens_limit > 0 && (
                                <span className="text-xs text-slate-500 tabular-nums">
                                    {u.period}: {u.period_tokens_used.toLocaleString()} / {u.period_tokens_limit.toLocaleString()} tokens ({quotaPct}%)
                                </span>
                            )}
                        </div>
                        {u.daily.length === 0
                            ? <p className="text-sm text-slate-400">No calls in this window.</p>
                            : <DailyTrend daily={u.daily} />}
                    </div>

                    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                        <h3 className="font-bold text-slate-800 mb-4">By provider &amp; model</h3>
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
                                        <TableCell><Badge variant="outline" className="rounded-md font-semibold bg-indigo-50 text-indigo-700 border-indigo-200">{p.provider}</Badge></TableCell>
                                        <TableCell className="font-mono text-xs text-slate-600">{p.model || '—'}</TableCell>
                                        <TableCell className="text-right tabular-nums">{p.calls.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-slate-500">{p.input_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-slate-500">{p.output_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums font-semibold">{p.total_tokens.toLocaleString()}</TableCell>
                                        <TableCell className="text-right tabular-nums text-slate-500">{fmtMs(p.avg_latency_ms)}</TableCell>
                                        <TableCell className="text-right tabular-nums">
                                            <span className={p.success_rate < 0.9 ? 'text-amber-600 font-semibold' : 'text-slate-600'}>{Math.round(p.success_rate * 100)}%</span>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </div>

                    <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm">
                        <h3 className="font-bold text-slate-800 mb-4">By feature</h3>
                        <div className="space-y-3 max-w-2xl">
                            {u.by_feature.map((f) => {
                                const share = u.totals.total_tokens ? (f.total_tokens / u.totals.total_tokens) * 100 : 0;
                                return (
                                    <div key={f.feature}>
                                        <div className="flex justify-between text-sm mb-1">
                                            <span className="text-slate-600">{FEATURE_LABELS[f.feature] || f.feature}</span>
                                            <span className="tabular-nums text-slate-500">{f.total_tokens.toLocaleString()} tokens · {f.calls} calls</span>
                                        </div>
                                        <div className="h-2 rounded-full bg-slate-100 overflow-hidden">
                                            <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.max(2, share)}%` }} />
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
