import { useQuery } from '@tanstack/react-query';
import { Printer, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import { LogoMark } from '@/components/redesign-local/groupF/AuthLayout';
import { fetchPublicShare, type PublicRunReport } from '@/api/reports';
import { SummaryView } from '@/components/reports/SummaryView';
import { fmtPct } from '@/components/reports/reportHelpers';
import { PocReportView } from '@/components/poc/PocReportView';

// Public, unauthenticated read-only report, rendered outside the app shell.
// Data: POST /api/share/view - a reduced view (names, statuses, counts).
// The link is /share#<token>: the fragment never reaches a server, so the
// token cannot end up in an access log; it is sent in the POST body instead.

const fmtMs = (ms: number | null) => (ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`);
const statusTone = (s: string) => (s === 'passed' ? 'mint' : s === 'failed' || s === 'error' ? 'peach' : 'neutral');

function RunReportView({ data }: { data: PublicRunReport }) {
    const r = data.run;
    return (
        <div className="flex flex-col gap-5">
            <section aria-label="Run" className="flex flex-col gap-3 rounded-3xl border border-border bg-white p-6">
                <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-soft">
                    {data.project_name && <span>{data.project_name}</span>}
                    {r.created_at && <><span>·</span><span>{new Date(r.created_at).toLocaleString()}</span></>}
                    {r.browser && <><span>·</span><span>{r.browser}</span></>}
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    <h2 className="text-xl font-semibold text-ink">{r.suite_name || `Run #${r.id}`}</h2>
                    <Pill tone={statusTone(r.status)}>{r.status}</Pill>
                </div>
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    {[['Pass rate', fmtPct(r.pass_rate)], ['Tests', r.total_tests], ['Failed', r.failed_tests], ['Duration', fmtMs(r.duration_ms)]].map(([l, v]) => (
                        <div key={String(l)} className="flex flex-col gap-1 rounded-2xl border border-border px-4 py-3">
                            <span className="text-[13px] text-ink-soft">{l}</span>
                            <span className="text-[22px] font-bold tabular-nums text-ink">{v}</span>
                        </div>
                    ))}
                </div>
            </section>
            <section aria-label="Tests" className="rounded-3xl border border-border bg-white p-5">
                <h2 className="mb-2 text-base font-semibold text-ink">Tests</h2>
                {data.results.length === 0 ? <p className="text-sm text-ink-soft">No test results.</p> : (
                    <ul className="flex flex-col">
                        {data.results.map((t, i) => (
                            <li key={`${t.test_name}-${i}`} className={`flex min-h-11 items-center justify-between gap-3 py-2 text-sm ${i > 0 ? 'border-t border-[#F0EEE8]' : ''}`}>
                                <span className="min-w-0 truncate text-ink" title={t.test_name}>{t.test_name}</span>
                                <span className="flex flex-none items-center gap-3">
                                    <span className="font-mono text-xs text-ink-soft tabular-nums">{fmtMs(t.duration_ms)}</span>
                                    <Pill tone={statusTone(t.status)}>{t.status}</Pill>
                                </span>
                            </li>
                        ))}
                    </ul>
                )}
            </section>
        </div>
    );
}

export default function PublicShare() {
    const token = decodeURIComponent(window.location.hash.replace(/^#/, '')) || undefined;
    // No retries: a 404 is the answer (expired, revoked, unknown), and the
    // public route is rate-limited.
    const q = useQuery({
        queryKey: ['public-share', token],
        queryFn: () => fetchPublicShare(token!),
        enabled: !!token,
        retry: false,
        refetchOnWindowFocus: false,
    });
    const data = q.data;
    const state = !token || q.isError ? 'missing' : q.isLoading ? 'loading' : 'ok';

    if (state === 'loading') {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background" aria-busy="true">
                <RefreshCw className="h-7 w-7 animate-spin text-ink-muted" aria-label="Loading report" />
            </div>
        );
    }
    if (state === 'missing' || !data) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background px-4">
                <div className="flex max-w-sm flex-col items-center gap-2 rounded-3xl border border-border bg-white px-6 py-12 text-center">
                    <p className="text-lg font-semibold text-ink">This link is not available</p>
                    <p className="text-sm text-ink-soft">It may have expired or been revoked. Ask whoever sent it for a new one.</p>
                </div>
            </div>
        );
    }

    const title = data.kind === 'run_report' ? 'Test run report' : data.kind === 'poc_report' ? 'Proof-of-concept report' : 'Quality summary';
    return (
        <div className="min-h-screen bg-background px-4 py-10 print:bg-white print:py-0 sm:py-14">
            <div className="mx-auto flex max-w-5xl flex-col gap-6">
                <header className="flex flex-wrap items-end justify-between gap-3">
                    <div className="flex flex-col gap-1">
                        <h1 className="text-[28px] font-bold leading-tight tracking-[-0.03em] text-ink">{data.label || title}</h1>
                        <span className="text-[13px] text-ink-soft">Read-only · shared from TraceIQ · link expires {new Date(data.expires_at).toLocaleDateString()}</span>
                    </div>
                    <Button variant="outline" className="print:hidden" onClick={() => window.print()}><Printer className="h-4 w-4" /> Print or save as PDF</Button>
                </header>
                {data.kind === 'run_report'
                    ? <RunReportView data={data} />
                    : data.kind === 'poc_report'
                        ? <>
                            <p className="text-sm text-ink-soft">{data.workspace_name} · {data.start} to {data.end}</p>
                            <PocReportView data={data} />
                        </>
                        : <SummaryView data={{ ...data, name: data.name ?? '' }} />}
                <footer className="flex flex-col items-center gap-3 pt-2 text-center text-xs text-ink-soft">
                    <span>Names, statuses and counts only: screenshots, traces, error messages and URLs are never included.</span>
                    <LogoMark />
                </footer>
            </div>
        </div>
    );
}
