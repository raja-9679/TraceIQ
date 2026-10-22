import type { CostReport } from '@/api/costs';
import { formatMoney, formatTokens } from './costFormat';

type Day = CostReport['daily'][number];

function Bars({ daily, value, label, peak, what }: {
    daily: Day[];
    value: (d: Day) => number;
    label: (d: Day) => string;
    peak: string;
    what: string;
}) {
    const max = Math.max(...daily.map(value));
    return (
        <div>
            <div className="mb-1 flex justify-between text-xs text-ink-soft tabular-nums">
                <span>{what}</span>
                <span>peak {peak} / day</span>
            </div>
            <div
                role="img"
                aria-label={`${what} per day from ${daily[0]?.date} to ${daily[daily.length - 1]?.date}, peak ${peak}`}
                className="flex h-36 items-end gap-[2px] border-b border-border"
            >
                {daily.map((d) => (
                    <div key={d.date} className="group relative flex h-full max-w-6 flex-1 items-end">
                        <div
                            className="w-full rounded-t-[4px] bg-ink/75 transition-colors group-hover:bg-ink"
                            style={{ height: `${value(d) > 0 ? Math.max(2, (value(d) / max) * 100) : 0}%` }}
                        />
                        <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1.5 hidden -translate-x-1/2 whitespace-nowrap rounded-lg bg-ink px-2.5 py-1.5 text-[11px] text-white shadow-lg group-hover:block">
                            {label(d)}
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

/** Daily spend, or — with no prices — daily token use and browser-minutes
 *  as two series (one axis can't hold both units). */
export default function CostTrend({ report }: { report: CostReport }) {
    const daily = report.daily;
    const tokens = (d: Day) => d.tokens;
    const minutes = (d: Day) => d.browser_minutes;
    const spend = (d: Day) => d.total ?? 0;
    const tooltip = (d: Day) => report.priced
        ? `${d.date}: ${formatMoney(d.total, report.currency)} (AI ${formatMoney(d.llm_amount, report.currency)}, workers ${formatMoney(d.browser_amount, report.currency)}), ${d.browser_minutes} browser-min`
        : `${d.date}: ${formatTokens(d.tokens)} tokens, ${d.browser_minutes} browser-min`;

    // An all-zero window used to draw an empty axis labelled "peak 1 tokens/day".
    const any = (f: (d: Day) => number) => daily.some((d) => f(d) > 0);
    if (!daily.length || !(any(tokens) || any(minutes) || any(spend))) {
        return <p className="text-sm text-ink-soft">No AI use or test time in this window.</p>;
    }

    if (report.priced) {
        return <Bars daily={daily} value={spend} label={tooltip} what="Spend"
            peak={formatMoney(Math.max(...daily.map(spend)), report.currency)} />;
    }
    return (
        <div className="flex flex-col gap-5">
            {any(tokens) && <Bars daily={daily} value={tokens} label={tooltip} what="AI tokens"
                peak={`${formatTokens(Math.max(...daily.map(tokens)))} tokens`} />}
            {any(minutes) && <Bars daily={daily} value={minutes} label={tooltip} what="Browser-minutes"
                peak={`${Math.max(...daily.map(minutes)).toLocaleString()} min`} />}
        </div>
    );
}
