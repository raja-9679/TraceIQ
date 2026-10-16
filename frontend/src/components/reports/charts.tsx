import type { ReactNode } from 'react';
import { areaPath, barSlots, linePath, niceMax, pctWidth } from './chartMath';

// Small inline-SVG charts for the WP12b report pages. Each is one role="img"
// with a text alternative; values the reader needs are printed beside them.

const INK = '#1C1B22';

export function Sparkline({ values, label, width = 120, height = 28, max, colour = INK }: {
    values: (number | null)[];
    label: string;
    width?: number;
    height?: number;
    max?: number;
    colour?: string;
}) {
    return (
        <svg role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} width={width} height={height} className="block flex-none">
            <line x1={0} x2={width} y1={height - 1} y2={height - 1} stroke="#EDEBE5" strokeWidth={1} />
            <path d={linePath(values, width, height, max)} fill="none" stroke={colour} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
        </svg>
    );
}

/** Area for the primary series, a line for the secondary (each on its own scale). */
export function TrendChart({ primary, secondary, label, height = 140, primaryColour = '#F2C94C', secondaryColour = INK }: {
    primary: (number | null)[];
    secondary?: (number | null)[];
    label: string;
    height?: number;
    primaryColour?: string;
    secondaryColour?: string;
}) {
    const width = 600;
    return (
        <svg role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="block h-[140px] w-full">
            <line x1={0} x2={width} y1={height - 2} y2={height - 2} stroke="#EDEBE5" strokeWidth={1} />
            <path d={areaPath(primary, width, height)} fill={primaryColour} fillOpacity={0.45} stroke={primaryColour} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
            {secondary && (
                <path d={linePath(secondary, width, height)} fill="none" stroke={secondaryColour} strokeWidth={1.8} vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
            )}
        </svg>
    );
}

/** Vertical bars, one per value; `colours` optionally per bar. Null = empty slot. */
export function BarStrip({ values, label, height = 48, max, colours, colour = INK }: {
    values: (number | null)[];
    label: string;
    height?: number;
    max?: number;
    colours?: string[];
    colour?: string;
}) {
    const width = Math.max(values.length * 8, 48);
    const top = max ?? niceMax(values);
    const slots = barSlots(values.length, width, values.length > 40 ? 1 : 2);
    return (
        <svg role="img" aria-label={label} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" className="block w-full" style={{ height }}>
            {values.map((v, i) => {
                if (v === null) {
                    return <rect key={i} x={slots[i].x} y={height - 2} width={slots[i].w} height={2} fill="#EDEBE5" />;
                }
                const h = Math.max(1, (v / top) * (height - 2));
                return <rect key={i} x={slots[i].x} y={height - h} width={slots[i].w} height={h} rx={1} fill={colours?.[i] ?? colour} />;
            })}
        </svg>
    );
}

/** A labelled horizontal bar list. */
export function HBarList({ rows, label }: {
    rows: { key: string; label: ReactNode; value: number; max: number; text: ReactNode; colour?: string }[];
    label: string;
}) {
    return (
        <ul aria-label={label} className="flex flex-col gap-2.5">
            {rows.map((r) => (
                <li key={r.key} className="flex flex-col gap-1">
                    <span className="flex items-baseline justify-between gap-3 text-sm">
                        <span className="min-w-0 break-words font-medium text-ink">{r.label}</span>
                        <span className="flex-none font-mono text-xs tabular-nums text-ink-soft">{r.text}</span>
                    </span>
                    <span className="block h-2 w-full overflow-hidden rounded-full bg-secondary" aria-hidden="true">
                        <span className="block h-full rounded-full" style={{ width: `${pctWidth(r.value, r.max)}%`, background: r.colour ?? INK }} />
                    </span>
                </li>
            ))}
        </ul>
    );
}

/** A full-width progress bar (error budget, compliance). */
export function Meter({ percent, colour, label }: { percent: number; colour: string; label: string }) {
    const p = Math.max(0, Math.min(100, percent));
    return (
        <span role="img" aria-label={label} className="block h-3 w-full overflow-hidden rounded-full bg-secondary">
            <span className="block h-full rounded-full" style={{ width: `${p}%`, background: colour }} />
        </span>
    );
}

export function Legend({ items }: { items: { colour: string; label: string }[] }) {
    return (
        <span className="flex flex-wrap gap-3 text-xs text-ink-soft">
            {items.map((i) => (
                <span key={i.label} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ background: i.colour }} aria-hidden="true" />
                    {i.label}
                </span>
            ))}
        </span>
    );
}
