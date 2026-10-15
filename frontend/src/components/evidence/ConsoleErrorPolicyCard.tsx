import { useState } from 'react';
import { Terminal } from 'lucide-react';
import { patternsFromText, type SuiteSettingsBlob } from './evidenceHelpers';

// Suite setting: fail a test on uncaught page errors / console.error.
// "Inherit" removes the key so the parent module's value applies (the
// backend carries it down the suite chain); a case can still override it.

export function ConsoleErrorPolicyCard({ settings, effective, inherit, onSave }: {
    settings: SuiteSettingsBlob | null | undefined;
    effective: SuiteSettingsBlob | null | undefined;
    inherit: boolean;
    onSave: (next: SuiteSettingsBlob, message: string) => void;
}) {
    const own = settings || {};
    const mode = own.fail_on_console_errors === true ? 'on' : own.fail_on_console_errors === false ? 'off' : 'inherit';
    const [patterns, setPatterns] = useState((own.console_error_ignore_patterns || []).join('\n'));
    const inheritedOn = inherit && mode === 'inherit' && effective?.fail_on_console_errors === true;

    const setMode = (value: string) => {
        const next: SuiteSettingsBlob = { ...own };
        if (value === 'inherit') delete next.fail_on_console_errors;
        else next.fail_on_console_errors = value === 'on';
        onSave(next, value === 'on' ? 'Tests now fail on JavaScript errors'
            : value === 'off' ? 'JavaScript errors no longer fail tests' : 'Inheriting the JavaScript-error setting');
    };
    const savePatterns = () => {
        const list = patternsFromText(patterns);
        const current = own.console_error_ignore_patterns || [];
        if (list.join('\n') === current.join('\n')) return;
        const next: SuiteSettingsBlob = { ...own };
        if (list.length) next.console_error_ignore_patterns = list;
        else delete next.console_error_ignore_patterns;
        onSave(next, 'Ignore patterns saved');
    };

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
            <div className="p-6 lg:p-8 grid md:grid-cols-3 gap-6 items-start">
                <div className="md:col-span-2 space-y-1">
                    <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                        <Terminal className="h-4 w-4 text-indigo-500" />
                        Fail on JavaScript errors
                    </h3>
                    <p className="text-sm text-slate-500">
                        Fail a test when the page throws an uncaught exception or logs <code>console.error</code>
                        during a step. Warnings never fail a test. Lines matching an ignore pattern are skipped —
                        one per line, plain text matches case-insensitively, <code>/regex/</code> as a regular expression.
                    </p>
                    {inheritedOn && <p className="text-xs text-indigo-600">Currently on, inherited from a parent module.</p>}
                </div>
                <div className="md:col-span-1 md:justify-self-end w-full space-y-3">
                    <label className="block text-xs font-semibold text-slate-600">
                        Behaviour
                        <select
                            value={mode}
                            onChange={(e) => setMode(e.target.value)}
                            className="mt-1 w-full text-sm border border-slate-200 rounded-xl px-3 h-11 bg-slate-50/50"
                        >
                            <option value="inherit">Inherit</option>
                            <option value="on">Fail the test</option>
                            <option value="off">Ignore errors</option>
                        </select>
                    </label>
                    <label className="block text-xs font-semibold text-slate-600">
                        Ignore patterns
                        <textarea
                            value={patterns}
                            onChange={(e) => setPatterns(e.target.value)}
                            onBlur={savePatterns}
                            rows={3}
                            placeholder={'favicon.ico\n/^ResizeObserver loop/'}
                            className="mt-1 w-full text-xs font-mono border border-slate-200 rounded-xl px-3 py-2 bg-slate-50/50"
                        />
                    </label>
                </div>
            </div>
        </div>
    );
}
