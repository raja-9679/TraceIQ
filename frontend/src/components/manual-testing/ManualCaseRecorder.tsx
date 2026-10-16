import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ImagePlus, Loader2, Paperclip, Save } from 'lucide-react';
import { toast } from 'sonner';
import { errorDetail } from '@/api/idpCatalog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { DetailCard, Eyebrow, Pill } from '@/components/redesign/primitives';
import { inputClass, textareaClass } from '@/components/redesign-local/groupD/settingsForm';
import { cn } from '@/lib/utils';
import { manualTestingApi, ManualOutcome, ManualResult } from '@/api/manualTesting';
import { OUTCOMES, outcomeMeta } from './manualSteps';

function OutcomePicker({ value, onChange, label, disabled, size = 'md' }: {
    value: ManualOutcome | null; onChange: (v: ManualOutcome) => void; label: string; disabled?: boolean; size?: 'sm' | 'md';
}) {
    return (
        <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
            {OUTCOMES.map((o) => (
                <button
                    key={o.value}
                    type="button"
                    role="radio"
                    aria-checked={value === o.value}
                    disabled={disabled}
                    onClick={() => onChange(o.value)}
                    className={cn(
                        'rounded-full border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60',
                        size === 'sm' ? 'h-8 px-3 text-xs' : 'h-10 px-4 text-sm',
                        value === o.value ? 'border-ink bg-ink text-white' : 'border-border bg-white text-ink hover:bg-background',
                    )}
                >
                    {o.label}
                </button>
            ))}
        </div>
    );
}

/** One case of a manual run: follow the steps, mark each, record the case. */
export default function ManualCaseRecorder({ runId, result, readOnly, onSaved }: {
    runId: number;
    result: ManualResult;
    readOnly: boolean;
    onSaved: (r: ManualResult) => void;
}) {
    // Initialised from the saved result; the parent remounts this (key) when
    // another case is selected or the saved result changes.
    const [outcome, setOutcome] = useState<ManualOutcome | null>(result.outcome);
    const [notes, setNotes] = useState(result.notes || '');
    const [minutes, setMinutes] = useState(result.duration_ms ? String(Math.round(result.duration_ms / 60000)) : '');
    const [stepOutcomes, setStepOutcomes] = useState<(ManualOutcome | null)[]>(
        () => result.steps.map((_, i) => result.step_results[i]?.outcome ?? null));
    const [stepNotes, setStepNotes] = useState<string[]>(
        () => result.steps.map((_, i) => result.step_results[i]?.note ?? ''));

    const save = useMutation({
        mutationFn: () => {
            const m = minutes.trim() === '' ? undefined : Math.max(0, Math.round(Number(minutes) * 60000));
            return manualTestingApi.record(runId, result.id, {
                outcome: outcome!,
                notes: notes.trim() || null,
                step_results: result.steps.map((_, i) => ({ outcome: stepOutcomes[i] ?? null, note: stepNotes[i]?.trim() || null })),
                ...(m !== undefined && Number.isFinite(m) ? { duration_ms: m } : {}),
            });
        },
        onSuccess: (r) => { toast.success(`Recorded ${outcomeMeta(r.outcome).label.toLowerCase()}`); onSaved(r); },
        onError: (err) => toast.error(errorDetail(err, 'Could not record the result')),
    });

    const attach = useMutation({
        mutationFn: (file: File) => manualTestingApi.attach(runId, result.id, file),
        onSuccess: (r) => { toast.success('Screenshot attached'); onSaved(r); },
        onError: (err) => toast.error(errorDetail(err, 'Could not attach the screenshot')),
    });

    const openAttachment = async (key: string) => {
        try {
            window.open(await manualTestingApi.artifactUrl(key), '_blank', 'noopener');
        } catch {
            toast.error('Could not open the attachment');
        }
    };

    const meta = outcomeMeta(result.outcome);
    const fileId = `manual-attach-${result.id}`;

    return (
        <DetailCard label={result.test_name}>
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                    <h2 className="text-xl font-semibold text-ink">{result.test_name}</h2>
                    <p className="mt-1 text-xs text-ink-soft">
                        {result.recorded_at
                            ? `Recorded by ${result.tester_name || 'someone'} · ${new Date(result.recorded_at).toLocaleString()}`
                            : 'Not recorded yet'}
                    </p>
                </div>
                <Pill tone={meta.tone}>{meta.label}</Pill>
            </div>

            <ol className="flex flex-col gap-3">
                {result.steps.map((s, i) => (
                    <li key={i} className="flex flex-col gap-3 rounded-2xl border border-border p-4">
                        <Eyebrow>Step {i + 1}</Eyebrow>
                        <p className="whitespace-pre-wrap text-[15px] text-ink">{s.instruction}</p>
                        {s.expected && (
                            <p className="whitespace-pre-wrap rounded-xl bg-background px-3 py-2 text-sm text-ink">
                                <span className="font-semibold">Expected: </span>{s.expected}
                            </p>
                        )}
                        <OutcomePicker size="sm" label={`Outcome of step ${i + 1}`} value={stepOutcomes[i] ?? null} disabled={readOnly}
                            onChange={(v) => setStepOutcomes((x) => { const n = [...x]; n[i] = v; return n; })} />
                        <Input className={inputClass} aria-label={`Note for step ${i + 1}`} placeholder="Note (optional)" disabled={readOnly}
                            value={stepNotes[i] ?? ''} onChange={(e) => setStepNotes((x) => { const n = [...x]; n[i] = e.target.value; return n; })} />
                    </li>
                ))}
            </ol>

            <div className="flex flex-col gap-3 border-t border-border pt-4">
                <Eyebrow>Result for this case</Eyebrow>
                <OutcomePicker label="Case outcome" value={outcome} disabled={readOnly} onChange={setOutcome} />
                <label className="flex flex-col gap-1.5 text-sm font-medium text-ink">
                    Notes
                    <textarea className={`${textareaClass} font-sans`} rows={3} disabled={readOnly} value={notes}
                        onChange={(e) => setNotes(e.target.value)} placeholder="What you saw — required context for failed or blocked" />
                </label>
                <label className="flex max-w-xs flex-col gap-1.5 text-sm font-medium text-ink">
                    Time spent, minutes (optional)
                    <Input type="number" min={0} className={inputClass} disabled={readOnly} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
                </label>
            </div>

            <div className="flex flex-col gap-2 border-t border-border pt-4">
                <Eyebrow>Screenshots</Eyebrow>
                {result.attachments.length === 0 ? (
                    <p className="text-sm text-ink-soft">None attached.</p>
                ) : (
                    <ul className="flex flex-col gap-1">
                        {result.attachments.map((a) => (
                            <li key={a.key}>
                                <Button variant="outline" size="sm" onClick={() => openAttachment(a.key)}>
                                    <Paperclip className="h-3.5 w-3.5" /> Open {a.name} ({Math.ceil(a.size / 1024)} KB)
                                </Button>
                            </li>
                        ))}
                    </ul>
                )}
                {!readOnly && (
                    <div>
                        <input id={fileId} type="file" accept="image/png,image/jpeg,image/gif,image/webp" className="sr-only"
                            onChange={(e) => { const f = e.target.files?.[0]; if (f) attach.mutate(f); e.target.value = ''; }} />
                        <Button variant="outline" asChild disabled={attach.isPending}>
                            <label htmlFor={fileId} className="cursor-pointer">
                                {attach.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImagePlus className="h-4 w-4" />}
                                Attach screenshot (PNG, JPEG, GIF or WebP, 5 MB)
                            </label>
                        </Button>
                    </div>
                )}
            </div>

            {!readOnly && (
                <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
                    <Button onClick={() => save.mutate()} disabled={!outcome || save.isPending}>
                        {save.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} Save result
                    </Button>
                    {!outcome && <span className="text-xs text-ink-soft">Pick an outcome for the case first.</span>}
                </div>
            )}
        </DetailCard>
    );
}
