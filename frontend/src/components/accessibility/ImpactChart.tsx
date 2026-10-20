import type { A11yDay } from '@/api/accessibility';
import { barSlots, niceMax } from '@/components/reports/chartMath';
import { IMPACT_COLOUR, dayTotal, stackDay } from './a11yModel';

/** Stacked daily bars: violations by impact (worst at the baseline). */
export function ImpactChart({ series, height = 140 }: { series: A11yDay[]; height?: number }) {
    const width = Math.max(series.length * 12, 120);
    const top = niceMax(series.map(dayTotal), 1);
    const slots = barSlots(series.length, width, series.length > 40 ? 1 : 2);
    const scale = (n: number) => (n / top) * (height - 2);
    return (
        <svg role="img" aria-label="Violations by impact per day" viewBox={`0 0 ${width} ${height}`}
            preserveAspectRatio="none" className="block w-full" style={{ height }}>
            {series.map((day, i) => (
                <g key={day.date}>
                    <title>{`${day.date}: ${day.critical} critical, ${day.serious} serious, ${day.moderate} moderate, ${day.minor} minor`}</title>
                    {stackDay(day).filter((s) => s.y1 > s.y0).map((s) => (
                        <rect key={s.impact} x={slots[i].x} width={slots[i].w}
                            y={height - scale(s.y1)} height={Math.max(1, scale(s.y1) - scale(s.y0))}
                            fill={IMPACT_COLOUR[s.impact]} />
                    ))}
                </g>
            ))}
        </svg>
    );
}
