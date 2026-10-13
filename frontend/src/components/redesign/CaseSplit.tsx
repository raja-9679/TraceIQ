import { useState, type ReactNode } from 'react';
import { CalendarClock, Download, Edit, FileText, Play, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DetailCard, Eyebrow, SplitView } from './primitives';
import { describeStep, type Step } from './steps';

// Direction A for a suite's test cases: the list on the left, the selected
// test's steps in plain words on the right with its actions. Pure layout —
// the suite page keeps owning every handler (run, schedule, edit, export,
// delete) and permission check.


export type CaseLike = { id: number; name: string; steps?: Step[] | null };

export function CaseSplit({
    cases, badge, canEdit, onRun, onSchedule, onEdit, onExport, onDelete,
}: {
    cases: CaseLike[];
    badge?: (c: CaseLike) => ReactNode;
    canEdit: boolean;
    onRun: (c: CaseLike) => void;
    onSchedule: (c: CaseLike) => void;
    onEdit: (c: CaseLike) => void;
    onExport: (c: CaseLike) => void;
    onDelete: (c: CaseLike) => void;
}) {
    const [selectedId, setSelectedId] = useState<number | null>(null);
    const selected = cases.find((c) => c.id === selectedId) ?? cases[0] ?? null;
    const steps = selected?.steps ?? [];

    return (
        <SplitView
            stickyList
            list={
                <div className="flex max-h-[calc(100vh-2rem)] flex-col overflow-hidden rounded-3xl border border-border bg-white">
                    <div className="flex items-baseline justify-between border-b border-[#F0EEE8] px-4 py-3">
                        <h2 className="text-sm font-semibold text-ink">Tests</h2>
                        <span className="font-mono text-xs text-ink-soft">{cases.length}</span>
                    </div>
                    <ul className="min-h-0 overflow-y-auto">
                        {cases.map((c) => {
                            const on = selected?.id === c.id;
                            const steps = c.steps?.length ?? 0;
                            return (
                                <li key={c.id} className="border-t border-[#F0EEE8] first:border-t-0">
                                    <button
                                        type="button"
                                        onClick={() => setSelectedId(c.id)}
                                        aria-pressed={on}
                                        className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${on ? 'bg-accent shadow-[inset_3px_0_0_#1C1B22]' : 'hover:bg-background'}`}
                                    >
                                        <span className={`flex h-8 w-8 flex-none items-center justify-center rounded-[10px] ${on ? 'bg-white text-ink' : 'bg-secondary text-ink-soft'}`} aria-hidden="true">
                                            <FileText className="h-4 w-4" />
                                        </span>
                                        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                                            <span className={`text-sm leading-snug text-ink ${on ? 'font-semibold' : 'font-medium'}`}>{c.name}</span>
                                            <span className="text-xs text-ink-soft">{steps} step{steps === 1 ? '' : 's'}</span>
                                        </span>
                                        {badge?.(c)}
                                    </button>
                                </li>
                            );
                        })}
                    </ul>
                </div>
            }
            detail={selected && (
                <DetailCard label="Selected test">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex min-w-0 flex-col gap-2">
                            <h2 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">{selected.name}</h2>
                            {badge?.(selected) && <div>{badge(selected)}</div>}
                        </div>
                        <Button size="lg" onClick={() => onRun(selected)}><Play className="h-4 w-4" fill="currentColor" /> Run now</Button>
                    </div>

                    <div className="flex flex-col gap-2">
                        <Eyebrow>Steps</Eyebrow>
                        {steps.length === 0 ? (
                            <p className="rounded-2xl bg-background p-4 text-sm text-ink-soft">No steps yet. Open the editor to add some.</p>
                        ) : (
                            <ol className="overflow-hidden rounded-2xl border border-border">
                                {steps.map((s, i) => {
                                    const d = describeStep(s);
                                    const isCheck = d.verb === 'Check';
                                    return (
                                        <li key={i} className="flex items-start gap-3 border-t border-[#F0EEE8] px-4 py-3.5 first:border-t-0">
                                            <span className="w-5 pt-0.5 font-mono text-xs text-ink-soft">{i + 1}</span>
                                            <span className={`w-20 flex-none truncate rounded-full px-2 py-0.5 text-center text-xs font-semibold capitalize ${isCheck ? 'bg-pastel-mint text-pastel-ink-mint' : 'bg-secondary text-ink-soft'}`}>
                                                {d.verb}
                                            </span>
                                            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                                                <span className="break-words text-[15px] leading-snug text-ink">{d.text}</span>
                                                {d.detail && <span className="break-all font-mono text-xs text-ink-soft">{d.detail}</span>}
                                            </span>
                                        </li>
                                    );
                                })}
                            </ol>
                        )}
                    </div>

                    <div className="flex flex-wrap gap-2.5">
                        {canEdit && <Button variant="outline" size="lg" onClick={() => onEdit(selected)}><Edit className="h-4 w-4" /> Edit steps</Button>}
                        <Button variant="outline" size="lg" onClick={() => onSchedule(selected)}><CalendarClock className="h-4 w-4" /> Schedule</Button>
                        <Button variant="outline" size="lg" onClick={() => onExport(selected)}><Download className="h-4 w-4" /> Export</Button>
                        {canEdit && (
                            <Button variant="outline" size="lg" className="sm:ml-auto" onClick={() => onDelete(selected)}>
                                <Trash2 className="h-4 w-4" /> Delete
                            </Button>
                        )}
                    </div>
                </DetailCard>
            )}
        />
    );
}
