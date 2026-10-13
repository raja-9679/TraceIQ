import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Info, Loader2, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    CaptureLevel,
    DataPolicy,
    DataPolicyView,
    getDataPolicy,
    updateDataPolicy,
} from '@/api/dataPolicy';
import { Pill } from '@/components/redesign/primitives';
import {
    CheckRow, FieldRow, checkboxClass, inputClass, textareaClass, warnCalloutClass,
} from '@/components/redesign-local/groupD/settingsForm';

/**
 * Per-project data-capture policy.
 *
 * The important thing this screen has to get right is honesty about the
 * instance ceiling: MAX_CAPTURE_LEVEL can hold a project below what it asked
 * for, and showing the requested level as if it were in force would tell an
 * operator they are recording video and traces when they are not.
 */

const LEVEL_BLURB: Record<CaptureLevel, string> = {
    none: 'Pass/fail and timing only. No screenshots, no logs.',
    minimal: 'Adds a masked screenshot when a test fails.',
    standard: 'Adds masked screenshots plus scrubbed console and network logs.',
    full: 'Adds video, Playwright traces and HAR — none of which can be redacted.',
};

/** Kinds worth calling out; the rest follow from the level. */
const SHOWN_KINDS: { key: string; label: string }[] = [
    { key: 'screenshot', label: 'Screenshots' },
    { key: 'console_log', label: 'Console logs' },
    { key: 'network_log', label: 'Network logs' },
    { key: 'video', label: 'Video' },
    { key: 'trace', label: 'Playwright trace' },
    { key: 'har', label: 'HAR' },
];

/** Textarea <-> string[] for the list fields. One entry per line. */
const toLines = (v?: string[] | null) => (v ?? []).join('\n');
const fromLines = (v: string) =>
    v.split('\n').map((s) => s.trim()).filter(Boolean);

export function DataPolicySection({ projectId }: { projectId: number }) {
    const query = useQuery({
        queryKey: ['data-policy', projectId],
        queryFn: () => getDataPolicy(projectId),
        enabled: !!projectId,
    });

    if (query.isLoading) {
        return (
            <section className="rounded-3xl border border-border bg-white p-6">
                <p className="flex items-center gap-2 text-sm text-ink-soft">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading data policy…
                </p>
            </section>
        );
    }
    if (query.isError || !query.data) {
        return (
            <section className="rounded-3xl border border-border bg-white p-6">
                <p className="text-sm text-destructive" role="alert">
                    Could not load the data policy for this project.
                </p>
            </section>
        );
    }

    // Re-key when the server's policy changes (after a save, or a project
    // switch) so the form re-seeds without mirroring query data in an effect.
    // Keyed on the content, not dataUpdatedAt, so a background refetch that
    // returns the same policy does not wipe unsaved edits — same as before.
    return (
        <DataPolicyForm
            key={`${projectId}-${JSON.stringify(query.data)}`}
            projectId={projectId}
            view={query.data}
        />
    );
}

const radioClass = 'mt-0.5 h-[18px] w-[18px] flex-none cursor-pointer accent-ink';

function DataPolicyForm({ projectId, view }: { projectId: number; view: DataPolicyView }) {
    const qc = useQueryClient();

    // Seed the form from the EFFECTIVE policy, so what you see is what runs.
    const [draft, setDraft] = useState<Partial<DataPolicy>>(() => ({
        capture_level: view.requested_capture_level,
        store_bodies: view.effective.store_bodies,
        redact_patterns: view.effective.redact_patterns,
        retention_days: view.effective.retention_days,
    }));
    const [headers, setHeaders] = useState(() => toLines(view.effective.redact_headers));
    const [bodyFields, setBodyFields] = useState(() => toLines(view.effective.redact_body_fields));
    const [selectors, setSelectors] = useState(() => toLines(view.effective.mask_selectors));

    const save = useMutation({
        mutationFn: () =>
            updateDataPolicy(projectId, {
                ...draft,
                redact_headers: fromLines(headers),
                redact_body_fields: fromLines(bodyFields),
                mask_selectors: fromLines(selectors),
            }),
        onSuccess: (saved) => {
            toast.success(
                saved.clamped
                    ? `Saved — capped at "${saved.effective.capture_level}" by the instance ceiling`
                    : 'Data policy saved',
            );
            qc.invalidateQueries({ queryKey: ['data-policy', projectId] });
        },
        onError: (e: any) =>
            toast.error(e?.response?.data?.detail ?? 'Could not save the data policy'),
    });

    const patterns = draft.redact_patterns ?? view.default_patterns;
    const usingDefaultPatterns = draft.redact_patterns === null;

    const togglePattern = (name: string) => {
        const current = new Set(patterns);
        if (current.has(name)) current.delete(name);
        else current.add(name);
        setDraft({ ...draft, redact_patterns: Array.from(current) });
    };

    const fieldId = (name: string) => `dp-${projectId}-${name}`;

    return (
        <section
            aria-labelledby={fieldId('title')}
            className="flex flex-col gap-5 rounded-3xl border border-border bg-white p-6"
        >
            <div>
                <h2 id={fieldId('title')} className="flex items-center gap-2 text-base font-semibold text-ink">
                    <ShieldCheck className="h-4 w-4 text-ink-soft" aria-hidden="true" />
                    Data capture and redaction
                </h2>
                <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">
                    What this project's runs are allowed to record, and what gets scrubbed
                    out of it before anything is stored.
                </p>
            </div>

            {view.clamped && (
                <div className={`flex items-start gap-2 ${warnCalloutClass}`} role="status">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                    <span>
                        This project requests <strong>{view.requested_capture_level}</strong>,
                        but the instance ceiling is{' '}
                        <strong>{view.instance_max_capture_level}</strong> — so{' '}
                        <strong>{view.effective.capture_level}</strong> is what actually
                        applies. Raise <code>MAX_CAPTURE_LEVEL</code> in Instance settings to
                        lift it.
                    </span>
                </div>
            )}

            {/* Capture level */}
            <fieldset className="flex flex-col gap-1">
                <legend className="mb-1 text-sm font-semibold text-ink">Capture level</legend>
                {view.available_capture_levels.map((level) => {
                    const blocked =
                        view.available_capture_levels.indexOf(level) >
                        view.available_capture_levels.indexOf(view.instance_max_capture_level);
                    const checked = draft.capture_level === level;
                    return (
                        <label
                            key={level}
                            className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-2xl border px-3 py-2.5 text-sm ${
                                checked ? 'border-border bg-background' : 'border-transparent hover:bg-background/60'
                            }`}
                        >
                            <input
                                type="radio"
                                name={fieldId('capture_level')}
                                className={radioClass}
                                checked={checked}
                                onChange={() => setDraft({ ...draft, capture_level: level })}
                            />
                            <span className="min-w-0">
                                <span className="flex flex-wrap items-center gap-2">
                                    <span className="font-medium text-ink">{level}</span>
                                    {blocked && <Pill tone="butter">Above the instance ceiling</Pill>}
                                </span>
                                <span className="block text-ink-soft">{LEVEL_BLURB[level]}</span>
                            </span>
                        </label>
                    );
                })}
            </fieldset>

            {/* What the effective level actually permits */}
            <div className="flex flex-col gap-2 rounded-2xl border border-border p-4">
                <h3 className="text-sm font-semibold text-ink">
                    Recorded right now <span className="font-normal text-ink-soft">(effective level: {view.effective.capture_level})</span>
                </h3>
                <ul className="flex flex-wrap gap-2" aria-label="What is recorded">
                    {SHOWN_KINDS.map(({ key, label }) => (
                        <li key={key}>
                            <Pill tone={view.permits[key] ? 'mint' : 'neutral'}>
                                {label}
                                <span className="sr-only">{view.permits[key] ? ': recorded' : ': not recorded'}</span>
                                {!view.permits[key] && <span aria-hidden="true">· off</span>}
                            </Pill>
                        </li>
                    ))}
                </ul>
                <p className="flex items-start gap-1.5 text-xs leading-relaxed text-ink-soft">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span>
                        Video, traces and HAR need <strong>full</strong>: a trace is a complete
                        DOM recording and a video captures whatever was on screen, so neither
                        can be meaningfully redacted.
                    </span>
                </p>
            </div>

            {/* Value patterns */}
            <fieldset className="flex flex-col gap-2">
                <legend className="mb-1 text-sm font-semibold text-ink">Redact these value patterns</legend>
                <div className="flex flex-wrap gap-x-5 gap-y-1">
                    {view.available_patterns.map((name) => (
                        <label key={name} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-ink">
                            <input
                                type="checkbox"
                                className={checkboxClass}
                                checked={patterns.includes(name)}
                                onChange={() => togglePattern(name)}
                            />
                            <span>{name}</span>
                        </label>
                    ))}
                </div>
                <p className="text-xs leading-relaxed text-ink-soft">
                    {usingDefaultPatterns
                        ? `Using the built-in set (${view.default_patterns.join(', ')}).`
                        : 'Card numbers and Aadhaar are checksum-validated, so an order id that happens to be 16 digits is left alone.'}
                    {' '}Email and phone are off by default — blanket-redacting them breaks
                    assertions that check for them.
                </p>
            </fieldset>

            {/* Field-name denylists + masks */}
            <div className="grid gap-4 border-t border-border pt-4 sm:grid-cols-3">
                <div className="flex flex-col gap-1.5">
                    <label htmlFor={fieldId('headers')} className="text-sm font-semibold text-ink">Extra headers</label>
                    <textarea
                        id={fieldId('headers')}
                        className={`h-24 ${textareaClass}`}
                        placeholder={'x-tenant-token\nx-internal-auth'}
                        value={headers}
                        onChange={(e) => setHeaders(e.target.value)}
                    />
                    <p className="text-xs leading-relaxed text-ink-soft">
                        One per line. Authorization, Cookie and the usual suspects are always
                        redacted.
                    </p>
                </div>
                <div className="flex flex-col gap-1.5">
                    <label htmlFor={fieldId('body')} className="text-sm font-semibold text-ink">Extra body fields</label>
                    <textarea
                        id={fieldId('body')}
                        className={`h-24 ${textareaClass}`}
                        placeholder={'policy_no\nnominee_dob'}
                        value={bodyFields}
                        onChange={(e) => setBodyFields(e.target.value)}
                    />
                    <p className="text-xs leading-relaxed text-ink-soft">
                        Matched regardless of case or separators, so <code>policy_no</code>{' '}
                        also covers <code>policyNo</code>.
                    </p>
                </div>
                <div className="flex flex-col gap-1.5">
                    <label htmlFor={fieldId('selectors')} className="text-sm font-semibold text-ink">Mask selectors</label>
                    <textarea
                        id={fieldId('selectors')}
                        className={`h-24 ${textareaClass}`}
                        placeholder={'#ssn\n[data-pii]'}
                        value={selectors}
                        onChange={(e) => setSelectors(e.target.value)}
                    />
                    <p className="text-xs leading-relaxed text-ink-soft">
                        CSS selectors painted over at capture time, so the pixels are never
                        written to disk.
                    </p>
                </div>
            </div>

            {/* Bodies + retention */}
            <div className="flex flex-col border-t border-border pt-3">
                <CheckRow
                    id={fieldId('store_bodies')}
                    label="Store request and response bodies"
                    hint="Off drops bodies entirely. Headers and status codes are kept either way, so failures stay triageable."
                    checked={draft.store_bodies !== false}
                    onChange={(v) => setDraft({ ...draft, store_bodies: v })}
                />
                <FieldRow
                    label="Retention (days)"
                    htmlFor={fieldId('retention')}
                    hint="0 falls back to the instance setting. Whichever window is shorter wins."
                >
                    <Input
                        id={fieldId('retention')}
                        type="number"
                        min={0}
                        className={`${inputClass} w-28`}
                        value={draft.retention_days ?? 0}
                        onChange={(e) =>
                            setDraft({ ...draft, retention_days: parseInt(e.target.value) || 0 })
                        }
                    />
                </FieldRow>
            </div>

            <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">
                <Button onClick={() => save.mutate()} disabled={save.isPending}>
                    {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    Save data policy
                </Button>
                <span className="text-xs text-ink-soft">
                    Takes effect on the next run dispatched for this project.
                </span>
            </div>
        </section>
    );
}
