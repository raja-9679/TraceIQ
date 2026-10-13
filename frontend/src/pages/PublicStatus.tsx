import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { API_BASE_URL } from '@/lib/api';
import { Pill } from '@/components/redesign/primitives';
import type { Tone } from '@/components/redesign/kinds';
import { LogoMark } from '@/components/redesign-local/groupF/AuthLayout';

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

const API_BASE = API_BASE_URL;

const OVERALL: Record<string, { label: string; tone: Tone }> = {
    operational: { label: 'All systems operational', tone: 'mint' },
    degraded: { label: 'Partial outage', tone: 'butter' },
    down: { label: 'Major outage', tone: 'peach' },
    unknown: { label: 'Status unknown', tone: 'neutral' },
};

const STATE: Record<StatusMonitor['state'], { label: string; tone: Tone }> = {
    up: { label: 'Up', tone: 'mint' },
    down: { label: 'Down', tone: 'peach' },
    unknown: { label: 'No data', tone: 'neutral' },
};

const fmtUptime = (v: number | null) => v == null ? '—' : `${v.toFixed(2)}%`;

// The public endpoint returns uptime percentages, not per-check history, so
// the strip shows the up/down split of the window rather than individual checks.
function UptimeStrip({ label, value }: { label: string; value: number | null }) {
    const up = value == null ? null : Math.max(0, Math.min(100, value));
    return (
        <div className="flex flex-col gap-1.5">
            <div className="flex justify-between text-xs text-ink-soft">
                <span>{label}</span>
                <span className="tabular-nums">{fmtUptime(value)}</span>
            </div>
            <div
                role="img"
                aria-label={up == null ? `${label}: no data` : `${label}: ${fmtUptime(value)} up`}
                className="flex h-2.5 gap-[3px] overflow-hidden rounded-full"
            >
                {up == null ? (
                    <span className="flex-1 rounded-full bg-secondary" />
                ) : (
                    <>
                        {up > 0 && <span className="rounded-full bg-[#8FD3B6]" style={{ flexGrow: up }} />}
                        {up < 100 && <span className="min-w-[4px] rounded-full bg-[#E0603A]" style={{ flexGrow: 100 - up }} />}
                    </>
                )}
            </div>
        </div>
    );
}

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
        return (
            <div className="flex min-h-screen items-center justify-center bg-background" aria-busy="true">
                <RefreshCw className="h-7 w-7 animate-spin text-ink-muted" aria-label="Loading status" />
            </div>
        );
    }
    if (error || !data) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background px-4">
                <div className="flex max-w-sm flex-col items-center gap-2 rounded-3xl border border-border bg-white px-6 py-12 text-center">
                    <p className="text-lg font-semibold text-ink">Status page not found</p>
                    <p className="text-sm text-ink-soft">The link may have been rotated or disabled.</p>
                </div>
            </div>
        );
    }

    const overall = OVERALL[data.overall] || OVERALL.unknown;

    return (
        <div className="min-h-screen bg-background px-4 py-10 sm:py-14">
            <div className="mx-auto flex max-w-2xl flex-col gap-6">
                <header className="flex flex-col gap-3">
                    <h1 className="text-[28px] font-bold leading-tight tracking-[-0.03em] text-ink">{data.title}</h1>
                    <Pill tone={overall.tone} className="self-start px-3 py-1 text-sm">{overall.label}</Pill>
                </header>

                <div className="flex flex-col gap-3">
                    {data.monitors.length === 0 && (
                        <div className="rounded-2xl border border-border bg-white px-5 py-6 text-sm text-ink-soft">
                            No monitors are published on this page yet.
                        </div>
                    )}
                    {data.monitors.map((m) => (
                        <section key={m.name} aria-label={m.name} className="flex flex-col gap-4 rounded-2xl border border-border bg-white px-5 py-4">
                            <div className="flex items-start justify-between gap-3">
                                <div className="flex min-w-0 flex-col gap-1">
                                    <h2 className="truncate text-[15px] font-semibold text-ink">{m.name}</h2>
                                    <span className="text-[13px] text-ink-soft">Uptime, last 24 hours</span>
                                    <span className="text-[26px] font-bold leading-none tracking-tight text-ink tabular-nums">{fmtUptime(m.uptime_24h)}</span>
                                </div>
                                <Pill tone={STATE[m.state]?.tone ?? 'neutral'} className="shrink-0">{STATE[m.state]?.label ?? m.state}</Pill>
                            </div>
                            <div className="grid gap-3 sm:grid-cols-2">
                                <UptimeStrip label="Last 24 hours" value={m.uptime_24h} />
                                <UptimeStrip label="Last 7 days" value={m.uptime_7d} />
                            </div>
                        </section>
                    ))}
                </div>

                <footer className="flex flex-col items-center gap-3 pt-2 text-center text-xs text-ink-soft">
                    <span>Updated every 60 seconds · Powered by TraceIQ synthetic monitoring</span>
                    <LogoMark />
                </footer>
            </div>
        </div>
    );
}
