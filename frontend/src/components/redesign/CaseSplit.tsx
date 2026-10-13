import { useState, type ReactNode } from 'react';
import { CalendarClock, Download, Edit, Play, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DetailCard, Eyebrow, KindChip, ListRow, SplitView } from './primitives';

// Direction A for a suite's test cases: the list on the left, the selected
// test's steps in plain words on the right with its actions. Pure layout —
// the suite page keeps owning every handler (run, schedule, edit, export,
// delete) and permission check.

type Step = { type?: string; selector?: string | null; value?: string | null; intent?: string | null } | string;

const isPlaceholder = (v: string) => /^\s*\{\{[^}]+\}\}\s*$/.test(v);

// Typed values can be credentials pasted into a step; show them only when
// they are a {{variable}} reference.
const q = (v: string) => `“${v}”`;

// Plain words for the common step types; anything else falls back to
// "<type> on <selector> with <value>". The label is a short verb for the chip.
function describeStep(step: Step): { verb: string; text: string; detail: string } {
    if (typeof step === 'string') return { verb: 'Step', text: step, detail: '' };
    const type = (step.type || 'step').toLowerCase();
    const sel = step.selector || '';
    const val = step.value ?? '';
    const typed = /fill|type|press|secret/.test(type);
    const v = val && (!typed || isPlaceholder(val)) ? val : val ? '(typed value)' : '';
    const on = sel ? ` ${sel}` : '';
    const map: Record<string, [string, string]> = {
        goto: ['Go', `Go to ${v || 'the page'}`],
        click: ['Click', `Click${on}`],
        hover: ['Hover', `Hover over${on}`],
        fill: ['Type', `Type ${v ? (v === '(typed value)' ? 'a value' : q(v)) : 'a value'} into${on}`],
        check: ['Tick', `Tick${on}`],
        'press-key': ['Press', `Press ${v}${sel ? ` in ${sel}` : ''}`],
        'select-option': ['Choose', `Choose ${q(v)} in${on}`],
        'wait-timeout': ['Wait', `Wait ${v} ms`],
        'expect-visible': ['Check', `Check${on} is visible`],
        'expect-hidden': ['Check', `Check${on} is hidden`],
        'expect-text': ['Check', `Check${on} shows ${q(v)}`],
        'expect-not-text': ['Check', `Check${on} does not show ${q(v)}`],
        'expect-title': ['Check', `Check the page title is ${q(v)}`],
        'expect-url': ['Check', `Check the URL contains ${q(v)}`],
        expect: ['Check', `Check${on}${v ? ` is ${q(v)}` : ''}`],
        assert: ['Check', `Check${on}${v ? ` is ${q(v)}` : ''}`],
        screenshot: ['Capture', `Take a screenshot${v ? ` ${q(v)}` : ''}`],
        'http-request': ['Request', `Send ${v || 'an HTTP request'}`],
    };
    const known = map[type];
    if (step.intent) return { verb: known?.[0] ?? 'Step', text: step.intent, detail: [sel, v].filter(Boolean).join(' · ') };
    if (known) return { verb: known[0], text: known[1], detail: '' };
    const verb = type.replace(/[-_]/g, ' ');
    return { verb: verb.split(' ')[0], text: [verb, sel && `on ${sel}`, v && `with ${v}`].filter(Boolean).join(' '), detail: '' };
}

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
            list={
                <div className="flex flex-col gap-0.5">
                    {cases.map((c) => (
                        <ListRow
                            key={c.id}
                            selected={selected?.id === c.id}
                            onSelect={() => setSelectedId(c.id)}
                            leading={<KindChip kind="review" size="sm" />}
                            title={c.name}
                            meta={`${c.steps?.length ?? 0} step${(c.steps?.length ?? 0) === 1 ? '' : 's'}`}
                            trailing={badge?.(c)}
                        />
                    ))}
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
