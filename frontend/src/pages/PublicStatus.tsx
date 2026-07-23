import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw, CheckCircle2, AlertTriangle, XCircle, HelpCircle } from 'lucide-react';

// Public, unauthenticated status page — rendered outside the app shell.
// Data comes from GET /api/status/{slug}; auto-refreshes every 60s.

interface StatusMonitor {
    name: string;
    state: 'up' | 'down' | 'unknown';
    uptime_24h: number | null;
    uptime_7d: number | null;
    last_checked_at: string | null;
}
interface StatusData {
    title: string;
    overall: 'operational' | 'degraded' | 'down' | 'unknown';
    monitors: StatusMonitor[];
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

const OVERALL: Record<string, { label: string; cls: string; icon: any }> = {
    operational: { label: 'All systems operational', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: CheckCircle2 },
    degraded: { label: 'Partial outage', cls: 'bg-amber-50 text-amber-700 border-amber-200', icon: AlertTriangle },
    down: { label: 'Major outage', cls: 'bg-rose-50 text-rose-700 border-rose-200', icon: XCircle },
    unknown: { label: 'Status unknown', cls: 'bg-slate-50 text-slate-600 border-slate-200', icon: HelpCircle },
};

const fmtUptime = (v: number | null) => v == null ? '—' : `${v.toFixed(2)}%`;

export default function PublicStatus() {
    const { slug } = useParams();
    const [data, setData] = useState<StatusData | null>(null);
    const [error, setError] = useState(false);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        let alive = true;
        const load = () => {
            fetch(`${API_BASE}/status/${slug}`)
                .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
                .then((d) => { if (alive) { setData(d); setError(false); setLoading(false); } })
                .catch(() => { if (alive) { setError(true); setLoading(false); } });
        };
        load();
        const timer = setInterval(load, 60_000);
        return () => { alive = false; clearInterval(timer); };
    }, [slug]);

    if (loading) {
        return <div className="min-h-screen bg-slate-50 flex items-center justify-center"><RefreshCw className="animate-spin text-slate-300 w-8 h-8" /></div>;
    }
    if (error || !data) {
        return (
            <div className="min-h-screen bg-slate-50 flex items-center justify-center">
                <div className="text-center">
                    <HelpCircle className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                    <p className="text-slate-600 font-semibold">Status page not found</p>
                    <p className="text-slate-400 text-sm mt-1">The link may have been rotated or disabled.</p>
                </div>
            </div>
        );
    }

    const overall = OVERALL[data.overall] || OVERALL.unknown;
    const OverallIcon = overall.icon;

    return (
        <div className="min-h-screen bg-slate-50 py-14 px-4">
            <div className="max-w-2xl mx-auto">
                <h1 className="text-3xl font-extrabold tracking-tight text-slate-900 mb-6">{data.title}</h1>
                <div className={`flex items-center gap-3 border rounded-2xl px-5 py-4 mb-8 ${overall.cls}`}>
                    <OverallIcon className="w-6 h-6 shrink-0" />
                    <span className="font-bold text-lg">{overall.label}</span>
                </div>

                <div className="bg-white border border-slate-200 rounded-2xl shadow-sm divide-y divide-slate-100">
                    {data.monitors.length === 0 && (
                        <p className="p-6 text-sm text-slate-400">No monitors are published on this page yet.</p>
                    )}
                    {data.monitors.map((m) => (
                        <div key={m.name} className="flex items-center justify-between px-5 py-4">
                            <div className="flex items-center gap-3 min-w-0">
                                <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${m.state === 'up' ? 'bg-emerald-500' : m.state === 'down' ? 'bg-rose-500' : 'bg-slate-300'}`} />
                                <span className="font-semibold text-slate-800 truncate">{m.name}</span>
                            </div>
                            <div className="flex items-center gap-6 text-sm tabular-nums text-slate-500 shrink-0">
                                <span title="Uptime, last 24h">{fmtUptime(m.uptime_24h)} <span className="text-slate-300 text-xs">24h</span></span>
                                <span title="Uptime, last 7 days" className="hidden sm:inline">{fmtUptime(m.uptime_7d)} <span className="text-slate-300 text-xs">7d</span></span>
                                <span className={`font-bold uppercase text-[10px] tracking-wider px-2.5 py-1 rounded-full border ${m.state === 'up' ? 'bg-emerald-50 text-emerald-700 border-emerald-100' : m.state === 'down' ? 'bg-rose-50 text-rose-700 border-rose-100' : 'bg-slate-50 text-slate-500 border-slate-200'}`}>
                                    {m.state}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>

                <p className="text-center text-xs text-slate-400 mt-8">
                    Updated every 60 seconds · Powered by TraceIQ synthetic monitoring
                </p>
            </div>
        </div>
    );
}
