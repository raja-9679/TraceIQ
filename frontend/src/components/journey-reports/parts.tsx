import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Small shared pieces for the WP12c report pages. White cards, colour only in
// small accents (the redesign rule).

export function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
    return (
        <div className="flex min-w-[150px] flex-1 flex-col gap-1 rounded-3xl border border-border bg-white px-5 py-4">
            <span className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">{label}</span>
            <span className="text-2xl font-bold tracking-[-0.02em] text-ink">{value}</span>
            {hint && <span className="text-xs text-ink-soft">{hint}</span>}
        </div>
    );
}

export function Card({ title, description, actions, children, className }: {
    title: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
    return (
        <section className={cn('flex min-w-0 flex-col gap-3 rounded-3xl border border-border bg-white p-5 sm:p-6', className)}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                    <h2 className="text-base font-semibold text-ink">{title}</h2>
                    {description && <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">{description}</p>}
                </div>
                {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
            </div>
            {children}
        </section>
    );
}

/** Plain table with the redesign's hairline rows; scrolls sideways on phones. */
export function DataTable({ head, children, label }: { head: ReactNode[]; children: ReactNode; label: string }) {
    return (
        <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-sm" aria-label={label}>
                <thead>
                    <tr className="border-b border-border text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
                        {head.map((h, i) => <th key={i} scope="col" className="px-2 py-2 font-semibold">{h}</th>)}
                    </tr>
                </thead>
                <tbody className="divide-y divide-[#F0EEE8]">{children}</tbody>
            </table>
        </div>
    );
}

/** Daily pass-rate bars (no chart library): grey = no runs that day. */
export function TrendBars({ points, label }: { points: { date: string; pass_rate: number | null; total: number }[]; label: string }) {
    return (
        <div className="flex h-8 items-end gap-[3px]" role="img" aria-label={label}>
            {points.map((p) => (
                <span
                    key={p.date}
                    title={`${p.date}: ${p.pass_rate === null ? 'no runs' : `${p.pass_rate}% of ${p.total}`}`}
                    className={cn('w-2 flex-none rounded-sm',
                        p.pass_rate === null ? 'h-1 bg-secondary' : p.pass_rate >= 100 ? 'bg-pastel-ink-mint' : p.pass_rate > 0 ? 'bg-pastel-ink-butter' : 'bg-pastel-ink-peach')}
                    style={p.pass_rate === null ? undefined : { height: `${Math.max(15, p.pass_rate)}%` }}
                />
            ))}
        </div>
    );
}

export function Loading({ children = 'Loading…' }: { children?: ReactNode }) {
    return <p className="rounded-3xl border border-border bg-white p-6 text-sm text-ink-soft">{children}</p>;
}
