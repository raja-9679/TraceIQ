import { AlertTriangle } from 'lucide-react';
import type { CleanupOutcome, HookOutcomes as Outcomes, HookPhase } from './suiteHooks';

const tone: Record<string, string> = {
    passed: 'text-emerald-700 bg-emerald-50',
    ok: 'text-emerald-700 bg-emerald-50',
    failed: 'text-rose-700 bg-rose-50',
    skipped: 'text-slate-600 bg-slate-100',
};

function Badge({ status }: { status: string }) {
    return <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${tone[status] || tone.skipped}`}>{status}</span>;
}

function PhaseRow({ phase }: { phase: HookPhase }) {
    return (
        <li className="space-y-1">
            <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge status={phase.status} />
                <span className="font-medium text-slate-800 capitalize">{phase.phase}</span>
                <span className="text-slate-500">{phase.suite_name || `suite ${phase.suite_id}`}</span>
                {phase.duration_ms !== undefined && <span className="text-xs text-slate-400">{phase.duration_ms} ms</span>}
            </div>
            {phase.error && <p className="ml-1 break-words font-mono text-xs text-rose-700">{phase.error}</p>}
            {phase.steps.length > 0 && (
                <ol className="ml-4 list-decimal space-y-0.5 text-xs text-slate-600">
                    {phase.steps.map((s) => (
                        <li key={s.index}>
                            {s.type} — {s.status}{s.error ? `: ${s.error}` : ''}
                        </li>
                    ))}
                </ol>
            )}
        </li>
    );
}

function CleanupRow({ item }: { item: CleanupOutcome }) {
    return (
        <li className="flex flex-wrap items-center gap-2 text-sm">
            <Badge status={item.status} />
            <span className="font-mono text-xs text-slate-700 break-all">{item.method} {item.url}</span>
            {item.http_status !== undefined && <span className="text-xs text-slate-500">HTTP {item.http_status}</span>}
            <span className="text-xs text-slate-400">{item.origin === 'setup' ? 'from suite setup' : 'from this test'}</span>
            {item.error && <span className="w-full break-words text-xs text-rose-700">{item.error}</span>}
        </li>
    );
}

// Suite setup/teardown and cleanup outcomes for one result.
export function HookOutcomes({ outcomes }: { outcomes: Outcomes }) {
    const phases = [...outcomes.setup, ...outcomes.teardown];
    return (
        <section aria-label="Setup, teardown and cleanup" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
            <h4 className="text-sm font-semibold text-slate-800">Setup, teardown &amp; cleanup</h4>
            {outcomes.warnings.length > 0 && (
                <div role="status" className="flex gap-2 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">
                    <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <ul className="space-y-1">{outcomes.warnings.map((w, i) => <li key={i} className="break-words">{w}</li>)}</ul>
                </div>
            )}
            {phases.length > 0 && <ul className="space-y-2">{phases.map((p, i) => <PhaseRow key={i} phase={p} />)}</ul>}
            {outcomes.cleanup.length > 0 && (
                <div className="space-y-1">
                    <h5 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Cleanup</h5>
                    <ul className="space-y-1">{outcomes.cleanup.map((c, i) => <CleanupRow key={i} item={c} />)}</ul>
                </div>
            )}
        </section>
    );
}

// Run-level line: the data seed the run used (with a replay action) and how
// many results had teardown/cleanup problems.
export function RunDataSummary({ seed, warningResults, onReplay, replaying }: {
    seed?: string | null;
    warningResults: number;
    onReplay?: () => void;
    replaying?: boolean;
}) {
    if (!seed && !warningResults) return null;
    return (
        <div className="flex flex-col gap-3 rounded-xl border border-slate-200 bg-white p-4 text-sm sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
                {seed && (
                    <p className="text-slate-700">
                        Data seed <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{seed}</code>
                        <span className="ml-2 text-xs text-slate-500">generated test data is reproducible with this seed</span>
                    </p>
                )}
                {warningResults > 0 && (
                    <p role="status" className="flex items-center gap-1.5 text-amber-700">
                        <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                        {warningResults} result{warningResults === 1 ? '' : 's'} had teardown or cleanup warnings
                    </p>
                )}
            </div>
            {seed && onReplay && (
                <button
                    type="button"
                    onClick={onReplay}
                    disabled={replaying}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                    {replaying ? 'Starting…' : 'Re-run with this seed'}
                </button>
            )}
        </div>
    );
}
