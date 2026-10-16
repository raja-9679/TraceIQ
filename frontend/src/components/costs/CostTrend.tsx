import type { CostReport } from '@/api/costs';
import { formatMoney, formatTokens } from './costFormat';

/** Daily spend (or, with no prices, daily usage) as one series of bars. */
export default function CostTrend({ report }: { report: CostReport }) {
    const daily = report.daily;
    const priced = report.priced;
    const value = (d: CostReport['daily'][number]) => (priced ? d.total ?? 0 : d.tokens);
    const max = Math.max(...daily.map(value), priced ? 0.01 : 1);
    const label = (d: CostReport['daily'][number]) => priced
        ? `${d.date}: ${formatMoney(d.total, report.currency)} (AI ${formatMoney(d.llm_amount, report.currency)}, workers ${formatMoney(d.browser_amount, report.currency)}), ${d.browser_minutes} browser-min`
        : `${d.date}: ${formatTokens(d.tokens)} tokens, ${d.browser_minutes} browser-min`;
    const peak = priced ? formatMoney(max, report.currency) : `${formatTokens(max)} tokens`;
    return (
        <div>
            <div className="mb-1 flex justify-between text-xs text-ink-soft tabular-nums">
                <span>0</span>
                <span>peak {peak} / day</span>
            </div>
            <div
                role="img"
                aria-label={`${priced ? 'Spend' : 'Token use'} per day from ${daily[0]?.date} to ${daily[daily.length - 1]?.date}, peak ${peak}`}
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
