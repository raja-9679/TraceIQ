import { RotateCcw } from 'lucide-react';
import type { SuiteSettingsBlob } from '@/components/evidence/evidenceHelpers';
import { RETRY_CHOICES, applyRetry, inheritedSummary, retryCount, retryMode, type RetryMode } from './retryPolicy';

// Suite setting: re-run a failed test automatically. "Inherit" removes the
// keys so the parent module's policy applies (the backend carries it down the
// suite chain). Same card layout as the HAR and JavaScript-error cards.
//
// The worker retries single-test jobs (executeSingleTestJobWithRetry). With
// distributed execution every mode dispatches tests as single jobs EXCEPT a
// Separate-mode sub-module with more than one test, which runs as one
// continuous job (backend/app/worker.py dispatch_separate_jobs) — no retries.

export function RetryPolicyCard({ settings, effective, inherit, executionMode, onSave }: {
    settings: SuiteSettingsBlob | null | undefined;
    effective: SuiteSettingsBlob | null | undefined;
    inherit: boolean;
    executionMode?: string;
    onSave: (next: SuiteSettingsBlob, message: string) => void;
}) {
    const own = settings || {};
    const mode = retryMode(own);
    const count = retryCount(own);

    const setMode = (value: RetryMode) => {
        onSave(applyRetry(own, value), value === 'on' ? `Failed tests now retry up to ${retryCount(applyRetry(own, 'on'))} times`
            : value === 'off' ? 'Automatic retries off' : 'Inheriting the retry setting');
    };
    const setCount = (n: number) => {
        onSave(applyRetry(own, 'on', n), `Failed tests now retry up to ${n} ${n === 1 ? 'time' : 'times'}`);
    };

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
            <div className="p-6 lg:p-8 grid md:grid-cols-3 gap-6 items-start">
                <div className="md:col-span-2 space-y-1">
                    <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                        <RotateCcw className="h-4 w-4 text-indigo-500" />
                        Retry failed tests
                    </h3>
                    <p className="text-sm text-slate-500">
                        Re-run a test that fails or errors, waiting a little longer before each try (1s, 2s, 4s…).
                        The run keeps the last attempt&apos;s result, and every attempt is recorded, so a test that
                        only passes on a retry is flagged as flaky rather than hidden. Inherited by sub-modules.
                    </p>
                    {executionMode === 'separate' && (
                        <p className="text-xs text-amber-700">
                            In Separate mode each sub-module with several tests runs them together as one job, and
                            those tests are not retried individually. Tests directly in this module are retried.
                        </p>
                    )}
                    {inherit && mode === 'inherit' && (
                        <p className="text-xs text-indigo-600">{inheritedSummary(effective)}</p>
                    )}
                </div>
                <div className="md:col-span-1 md:justify-self-end w-full space-y-3">
                    <label className="block text-xs font-semibold text-slate-600">
                        Behaviour
                        <select
                            value={mode}
                            onChange={(e) => setMode(e.target.value as RetryMode)}
                            className="mt-1 w-full text-sm border border-slate-200 rounded-xl px-3 h-11 bg-slate-50/50"
                        >
                            <option value="inherit">Inherit</option>
                            <option value="on">Retry failed tests</option>
                            <option value="off">Don&apos;t retry</option>
                        </select>
                    </label>
                    {mode === 'on' && (
                        <label className="block text-xs font-semibold text-slate-600">
                            Retries per test
                            <select
                                value={count}
                                onChange={(e) => setCount(Number(e.target.value))}
                                className="mt-1 w-full text-sm border border-slate-200 rounded-xl px-3 h-11 bg-slate-50/50"
                            >
                                {RETRY_CHOICES.map((n) => <option key={n} value={n}>{n}</option>)}
                                {!RETRY_CHOICES.includes(count as typeof RETRY_CHOICES[number]) && (
                                    <option value={count}>{count}</option>
                                )}
                            </select>
                        </label>
                    )}
                </div>
            </div>
        </div>
    );
}
