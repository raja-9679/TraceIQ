import { sparklinePoints } from './helpers';

interface Props {
    values: number[];
    label: string;
}

/** Score trend, 0–100, oldest left. */
export default function Sparkline({ values, label }: Props) {
    if (values.length === 0) return <span className="text-xs text-slate-400">no history</span>;
    const w = 80, h = 20;
    return (
        <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`${label}: ${values.join(', ')}`}
            className="overflow-visible">
            <polyline points={sparklinePoints(values, w, h)} fill="none" stroke="currentColor" strokeWidth={1.5}
                className="text-indigo-500" />
        </svg>
    );
}
