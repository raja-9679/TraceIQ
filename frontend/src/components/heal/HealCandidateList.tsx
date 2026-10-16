import type { HealCandidate, HealRecheck } from '@/api/proposals';
import { Button } from '@/components/ui/button';
import { Pill } from '@/components/redesign/primitives';
import { cn } from '@/lib/utils';
import {
    BREAKDOWN_LABEL, STRATEGY_LABEL, matchesLabel, pct, recheckLabel, recheckTone,
} from './healCandidates';

/**
 * Ranked heal candidates with their evidence. A reviewer may pick a
 * different candidate (radio) before accepting, and check it against the
 * stored DOM snapshot first.
 */
export default function HealCandidateList({
    candidates, picked, onPick, legacy, editable, onRecheck, recheck, checking,
}: {
    candidates: HealCandidate[];
    picked: string;
    onPick: (selector: string) => void;
    /** Pre-W5 proposal: one selector, no counts. */
    legacy: boolean;
    editable: boolean;
    onRecheck?: () => void;
    recheck?: HealRecheck | null;
    checking?: boolean;
}) {
    return (
        <div className="flex flex-col gap-3">
            <fieldset className="flex flex-col gap-2" disabled={!editable}>
                <legend className="sr-only">Replacement selector</legend>
                {candidates.map((c, i) => {
                    const id = `heal-cand-${i}`;
                    const isPicked = c.selector === picked;
                    return (
                        <label
                            key={c.selector}
                            htmlFor={id}
                            className={cn(
                                'flex cursor-pointer items-start gap-3 rounded-2xl border bg-white px-4 py-3',
                                isPicked ? 'border-ink' : 'border-border',
                                !editable && 'cursor-default',
                            )}
                        >
                            <input
                                id={id}
                                type="radio"
                                name="heal-candidate"
                                className="mt-1"
                                checked={isPicked}
                                disabled={!editable}
                                onChange={() => onPick(c.selector)}
                                aria-label={`Use ${c.selector}`}
                            />
                            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-xs font-semibold text-ink-soft">#{i + 1}</span>
                                    <code className="min-w-0 break-all rounded-lg bg-[#F6F5F1] px-2 py-0.5 font-mono text-xs text-ink">
                                        {c.selector}
                                    </code>
                                </div>
                                <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-soft">
                                    <Pill tone="neutral">{STRATEGY_LABEL[c.strategy] ?? c.strategy}</Pill>
                                    {c.source === 'both' && <Pill tone="lavender">AI agrees</Pill>}
                                    {legacy ? (
                                        <Pill tone="neutral">{matchesLabel(c, true)}</Pill>
                                    ) : (
                                        <Pill tone={c.applicable ? 'mint' : 'pink'}>{matchesLabel(c, false)}</Pill>
                                    )}
                                    <span className="font-mono tabular-nums" title="Score">Score {pct(c.score)}</span>
                                </div>
                                {!legacy && (
                                    <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-ink-soft sm:grid-cols-4">
                                        {(Object.keys(BREAKDOWN_LABEL) as Array<keyof typeof BREAKDOWN_LABEL>).map((k) => (
                                            <div key={k} className="flex justify-between gap-2">
                                                <dt>{BREAKDOWN_LABEL[k]}</dt>
                                                <dd className="font-mono tabular-nums">{pct(c.breakdown[k])}</dd>
                                            </div>
                                        ))}
                                    </dl>
                                )}
                            </div>
                        </label>
                    );
                })}
            </fieldset>
            {(onRecheck || recheck) && (
                <div className="flex flex-wrap items-center gap-2.5">
                    {onRecheck && editable && (
                        <Button variant="outline" size="sm" disabled={checking} onClick={onRecheck}>
                            {checking ? 'Checking…' : 'Check uniqueness'}
                        </Button>
                    )}
                    {recheck && (
                        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2" role="status">
                            <Pill tone={recheckTone(recheck)}>{recheckLabel(recheck)}</Pill>
                            <span className="text-xs text-ink-soft">{recheck.detail}</span>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
