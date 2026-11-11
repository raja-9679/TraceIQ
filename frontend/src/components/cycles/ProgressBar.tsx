import type { Progress } from '@/api/cycles';
import { barSegments, stateBar, stateLabel, STATES } from './model';

/** Stacked bar of a cycle's cases by state. The same numbers are given as text
 * (the bar alone is not readable by a screen reader, or without colour). */
export function ProgressBar({ progress, legend = false }: { progress: Progress; legend?: boolean }) {
    const segments = barSegments(progress);
    const text = STATES.filter((s) => progress.counts[s] > 0)
        .map((s) => `${progress.counts[s]} ${stateLabel[s].toLowerCase()}`).join(', ') || 'no cases';
    return (
        <div className="flex flex-col gap-2">
            <div role="img" aria-label={`Progress: ${text}`}
                className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full bg-neutral-100">
                {segments.map((s) => (
                    <span key={s.state} className={stateBar[s.state]} style={{ width: `${s.width}%`, flexGrow: s.count }} />
                ))}
            </div>
            {legend && (
                <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-ink-soft">
                    {STATES.map((s) => (
                        <li key={s} className="flex items-center gap-1.5">
                            <span aria-hidden="true" className={`h-2.5 w-2.5 rounded-full ${stateBar[s]}`} />
                            <span><strong className="font-semibold text-ink">{progress.counts[s]}</strong> {stateLabel[s].toLowerCase()}</span>
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
