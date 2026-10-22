import { useQuery } from '@tanstack/react-query';
import { Activity, AlertCircle, RefreshCw } from 'lucide-react';
import { serverMetricsApi, ServerMetricSeries } from '@/api/serverMetrics';

const COLORS = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#0ea5e9', '#a855f7'];
const W = 260;
const H = 56;

const num = (v: number) => {
    if (!Number.isFinite(v)) return String(v);
    const a = Math.abs(v);
    if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
    return `${Math.round(v * 100) / 100}`;
};

function SeriesChart({ series }: { series: ServerMetricSeries }) {
    const lines = series.result.map((r) => r.points.filter(([, v]) => Number.isFinite(v)));
    const all = lines.flat();
    if (!all.length) return <p className="text-xs text-slate-400">no data in the window</p>;
    const xs = all.map(([t]) => t);
    const ys = all.map(([, v]) => v);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
    const [y0, y1] = [Math.min(0, ...ys), Math.max(...ys)];
    const sx = (t: number) => x1 === x0 ? W / 2 : ((t - x0) / (x1 - x0)) * W;
    const sy = (v: number) => y1 === y0 ? H / 2 : H - ((v - y0) / (y1 - y0)) * H;
    const latest = lines.map((l) => l[l.length - 1]?.[1]).filter((v) => v !== undefined) as number[];
    return (
        <div>
            <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-14 bg-slate-50 rounded"
                role="img" aria-label={`${series.name} over the test window`}>
                {lines.map((l, i) => (
                    <polyline key={i} fill="none" stroke={COLORS[i % COLORS.length]} strokeWidth={1.5}
                        vectorEffect="non-scaling-stroke"
                        points={l.map(([t, v]) => `${sx(t).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')} />
                ))}
            </svg>
            <div className="flex justify-between text-[10px] text-slate-400 tabular-nums mt-1">
                <span>{series.result.length} series</span>
                <span>latest {latest.length === 1 ? num(latest[0]) : latest.map(num).join(' / ')} · max {num(y1)}</span>
            </div>
        </div>
    );
}

export default function ServerMetricsPanel({ runId, resultId }: { runId: number | string; resultId: number }) {
    const { data, isLoading, error, refetch, isFetching } = useQuery({
        queryKey: ['server-metrics', runId, resultId],
        queryFn: () => serverMetricsApi.get(runId, resultId),
        retry: false,
    });

    return (
        <div className="mt-4">
            <div className="flex items-center justify-between mb-2">
                <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                    <Activity size={12} /> Server metrics (Prometheus, over the test window)
                </p>
                <button type="button" onClick={() => refetch()} aria-label="Refresh server metrics"
                    className="text-slate-400 hover:text-slate-700">
                    <RefreshCw size={12} className={isFetching ? 'animate-spin' : ''} />
                </button>
            </div>
            {isLoading ? (
                <RefreshCw className="animate-spin w-4 h-4 text-slate-400" />
            ) : error ? (
                <p className="text-xs text-rose-600 flex items-center gap-1">
                    <AlertCircle size={12} /> {(error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Could not load server metrics'}
                </p>
            ) : !data ? null : !data.configured ? (
                <p className="text-xs text-slate-500">
                    An instance admin sets the Prometheus URL in Setup → Instance settings → Observability to see server-side series here.
                </p>
            ) : data.queries_defined === 0 ? (
                <p className="text-xs text-slate-500">
                    No queries attached. Add <code className="bg-slate-100 px-1 rounded">params.prometheus_queries</code>{' '}
                    (a list of {'{name, query}'} PromQL expressions) to this case's load-test step.
                </p>
            ) : (
                <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {data.series.map((s) => (
                        <div key={s.name + s.query} className="border border-slate-200 rounded-xl p-3">
                            <div className="text-xs font-semibold text-slate-700">{s.name}</div>
                            <code className="block text-[10px] text-slate-400 truncate mb-2" title={s.query}>{s.query}</code>
                            {s.error ? <p className="text-xs text-rose-600 break-words">{s.error}</p> : <SeriesChart series={s} />}
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
