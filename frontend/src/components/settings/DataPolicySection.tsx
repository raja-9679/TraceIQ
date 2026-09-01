import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Info, Loader2, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    CaptureLevel,
    DataPolicy,
    getDataPolicy,
    updateDataPolicy,
} from '@/api/dataPolicy';

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
    const qc = useQueryClient();
    const query = useQuery({
        queryKey: ['data-policy', projectId],
        queryFn: () => getDataPolicy(projectId),
        enabled: !!projectId,
    });

    const [draft, setDraft] = useState<Partial<DataPolicy> | null>(null);
    const [headers, setHeaders] = useState('');
    const [bodyFields, setBodyFields] = useState('');
    const [selectors, setSelectors] = useState('');

    // Seed the form from the EFFECTIVE policy, so what you see is what runs.
    useEffect(() => {
        if (!query.data) return;
        const e = query.data.effective;
        setDraft({
            capture_level: query.data.requested_capture_level,
            store_bodies: e.store_bodies,
            redact_patterns: e.redact_patterns,
            retention_days: e.retention_days,
        });
        setHeaders(toLines(e.redact_headers));
        setBodyFields(toLines(e.redact_body_fields));
        setSelectors(toLines(e.mask_selectors));
    }, [query.data]);

    const save = useMutation({
        mutationFn: () =>
            updateDataPolicy(projectId, {
                ...draft,
                redact_headers: fromLines(headers),
                redact_body_fields: fromLines(bodyFields),
                mask_selectors: fromLines(selectors),
            }),
        onSuccess: (view) => {
            toast.success(
                view.clamped
                    ? `Saved — capped at "${view.effective.capture_level}" by the instance ceiling`
                    : 'Data policy saved',
            );
            qc.invalidateQueries({ queryKey: ['data-policy', projectId] });
        },
        onError: (e: any) =>
            toast.error(e?.response?.data?.detail ?? 'Could not save the data policy'),
    });

    if (query.isLoading) {
        return (
            <div className="flex items-center gap-2 text-sm text-slate-500 p-5">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading data policy…
            </div>
        );
    }
    if (query.isError || !query.data || !draft) {
        return (
            <div className="text-sm text-red-600 p-5">
                Could not load the data policy for this project.
            </div>
        );
    }

    const view = query.data;
    const patterns = draft.redact_patterns ?? view.default_patterns;
    const usingDefaultPatterns = draft.redact_patterns === null;

    const togglePattern = (name: string) => {
        const current = new Set(patterns);
        current.has(name) ? current.delete(name) : current.add(name);
        setDraft({ ...draft, redact_patterns: Array.from(current) });
    };

    return (
        <div className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-5">
            <div>
                <h3 className="font-bold text-slate-800 flex items-center gap-2">
                    <ShieldCheck className="h-4 w-4 text-slate-500" />
                    Data capture &amp; redaction
                </h3>
                <p className="text-sm text-slate-500 mt-1">
                    What this project's runs are allowed to record, and what gets scrubbed
                    out of it before anything is stored.
                </p>
            </div>

            {view.clamped && (
                <div className="flex items-start gap-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
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
            <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700">Capture level</label>
                <div className="space-y-1">
                    {view.available_capture_levels.map((level) => {
                        const blocked =
                            view.available_capture_levels.indexOf(level) >
                            view.available_capture_levels.indexOf(view.instance_max_capture_level);
                        return (
                            <label
                                key={level}
                                className={`flex items-start gap-2 text-sm rounded-md px-2 py-1.5 cursor-pointer ${
                                    draft.capture_level === level ? 'bg-slate-50' : ''
                                }`}
                            >
                                <input
                                    type="radio"
                                    name="capture_level"
                                    className="mt-1"
                                    checked={draft.capture_level === level}
                                    onChange={() => setDraft({ ...draft, capture_level: level })}
                                />
                                <span>
                                    <span className="font-medium text-slate-800">{level}</span>
                                    {blocked && (
                                        <span className="ml-2 text-xs text-amber-700">
                                            above the instance ceiling
                                        </span>
                                    )}
                                    <span className="block text-slate-500">{LEVEL_BLURB[level]}</span>
                                </span>
                            </label>
                        );
                    })}
                </div>
            </div>

            {/* What the effective level actually permits */}
            <div className="border border-slate-200 rounded-md p-3">
                <div className="text-xs font-medium text-slate-600 mb-2">
                    Recorded right now (effective level: {view.effective.capture_level})
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 text-sm">
                    {SHOWN_KINDS.map(({ key, label }) => (
                        <div key={key} className="flex items-center gap-1.5">
                            <span
                                className={`h-1.5 w-1.5 rounded-full ${
                                    view.permits[key] ? 'bg-emerald-500' : 'bg-slate-300'
                                }`}
                            />
                            <span className={view.permits[key] ? 'text-slate-700' : 'text-slate-400'}>
                                {label}
                            </span>
                        </div>
                    ))}
                </div>
                <p className="text-xs text-slate-500 mt-2 flex items-start gap-1.5">
                    <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                    Video, traces and HAR need <strong>full</strong>: a trace is a complete
                    DOM recording and a video captures whatever was on screen, so neither
                    can be meaningfully redacted.
                </p>
            </div>

            {/* Value patterns */}
            <div className="space-y-2">
                <label className="text-sm font-medium text-slate-700">
                    Redact these value patterns
                </label>
                <div className="flex flex-wrap gap-3">
                    {view.available_patterns.map((name) => (
                        <label key={name} className="flex items-center gap-1.5 text-sm">
                            <input
                                type="checkbox"
                                checked={patterns.includes(name)}
                                onChange={() => togglePattern(name)}
                            />
                            <span className="text-slate-700">{name}</span>
                        </label>
                    ))}
                </div>
                <p className="text-xs text-slate-500">
                    {usingDefaultPatterns
                        ? `Using the built-in set (${view.default_patterns.join(', ')}).`
                        : 'Card numbers and Aadhaar are checksum-validated, so an order id that happens to be 16 digits is left alone.'}
                    {' '}Email and phone are off by default — blanket-redacting them breaks
                    assertions that check for them.
                </p>
            </div>

            {/* Field-name denylists + masks */}
            <div className="grid sm:grid-cols-3 gap-4">
                <div className="space-y-1">
                    <label className="text-sm font-medium text-slate-700">Extra headers</label>
                    <textarea
                        className="w-full h-24 text-sm border border-slate-200 rounded-md p-2 font-mono"
                        placeholder={'x-tenant-token\nx-internal-auth'}
                        value={headers}
                        onChange={(e) => setHeaders(e.target.value)}
                    />
                    <p className="text-xs text-slate-500">
                        One per line. Authorization, Cookie and the usual suspects are always
                        redacted.
                    </p>
                </div>
                <div className="space-y-1">
                    <label className="text-sm font-medium text-slate-700">Extra body fields</label>
                    <textarea
                        className="w-full h-24 text-sm border border-slate-200 rounded-md p-2 font-mono"
                        placeholder={'policy_no\nnominee_dob'}
                        value={bodyFields}
                        onChange={(e) => setBodyFields(e.target.value)}
                    />
                    <p className="text-xs text-slate-500">
                        Matched regardless of case or separators, so <code>policy_no</code>{' '}
                        also covers <code>policyNo</code>.
                    </p>
                </div>
                <div className="space-y-1">
                    <label className="text-sm font-medium text-slate-700">Mask selectors</label>
                    <textarea
                        className="w-full h-24 text-sm border border-slate-200 rounded-md p-2 font-mono"
                        placeholder={'#ssn\n[data-pii]'}
                        value={selectors}
                        onChange={(e) => setSelectors(e.target.value)}
                    />
                    <p className="text-xs text-slate-500">
                        CSS selectors painted over at capture time, so the pixels are never
                        written to disk.
                    </p>
                </div>
            </div>

            {/* Bodies + retention */}
            <div className="grid sm:grid-cols-2 gap-4">
                <label className="flex items-start gap-2 text-sm">
                    <input
                        type="checkbox"
                        className="mt-1"
                        checked={draft.store_bodies !== false}
                        onChange={(e) => setDraft({ ...draft, store_bodies: e.target.checked })}
                    />
                    <span>
                        <span className="font-medium text-slate-700">
                            Store request &amp; response bodies
                        </span>
                        <span className="block text-xs text-slate-500">
                            Off drops bodies entirely. Headers and status codes are kept either
                            way, so failures stay triageable.
                        </span>
                    </span>
                </label>
                <label className="text-sm space-y-1">
                    <span className="font-medium text-slate-700 block">Retention (days)</span>
                    <Input
                        type="number"
                        min={0}
                        className="w-28 h-8"
                        value={draft.retention_days ?? 0}
                        onChange={(e) =>
                            setDraft({ ...draft, retention_days: parseInt(e.target.value) || 0 })
                        }
                    />
                    <span className="block text-xs text-slate-500">
                        0 falls back to the instance setting. Whichever window is shorter wins.
                    </span>
                </label>
            </div>

            <div className="flex items-center gap-3 pt-1">
                <Button onClick={() => save.mutate()} disabled={save.isPending}>
                    {save.isPending && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
                    Save data policy
                </Button>
                <span className="text-xs text-slate-500">
                    Takes effect on the next run dispatched for this project.
                </span>
            </div>
        </div>
    );
}
