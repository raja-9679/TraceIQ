import { useState } from 'react';
import { ListChecks, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { StepComponent, type TestStep } from '@/components/test-builder/StepComponent';
import {
    firstUnchosen, hookStepsOf, moveItem, newHookStep, withHooks, type HookKey, type SuiteHookSettings,
} from './suiteHooks';

function HookStepList({ label, steps, onChange }: {
    label: string;
    steps: TestStep[];
    onChange: (next: TestStep[]) => void;
}) {
    const update = (id: string, field: keyof TestStep, value: unknown) =>
        onChange(steps.map((s) => (s.id === id ? { ...s, [field]: value } : s)));
    const insertAfter = (index: number) => {
        const next = [...steps];
        next.splice(index + 1, 0, newHookStep());
        onChange(next);
    };
    return (
        <section aria-label={label} className="space-y-3">
            <div className="flex items-center justify-between gap-3">
                <h4 className="text-sm font-semibold text-slate-800">{label}</h4>
                <Button type="button" variant="outline" size="sm" onClick={() => onChange([...steps, newHookStep()])}>
                    <Plus className="mr-1 h-4 w-4" /> Add {label.toLowerCase()} step
                </Button>
            </div>
            {steps.length === 0 && <p className="text-xs text-slate-500">No {label.toLowerCase()} steps.</p>}
            <div className="space-y-4">
                {steps.map((step, index) => (
                    <StepComponent
                        key={step.id}
                        step={step}
                        index={index}
                        updateStep={update}
                        removeStep={(id) => onChange(steps.filter((s) => s.id !== id))}
                        moveStep={(i, direction) => onChange(moveItem(steps, i, direction))}
                        insertStep={insertAfter}
                        isFirst={index === 0}
                        isLast={index === steps.length - 1}
                    />
                ))}
            </div>
        </section>
    );
}

// Suite settings: steps run before (setup) and after (teardown) this suite's
// cases, plus the {{fake.*}} data seed. Saved together with one button so a
// half-edited step list is never sent to a run.
export function SuiteHooksCard({ settings, effectiveSeed, inherit, onSave }: {
    settings: SuiteHookSettings | null | undefined;
    /** data_seed after inheritance (suite.effective_settings.data_seed). */
    effectiveSeed?: string | null;
    inherit: boolean;
    onSave: (next: SuiteHookSettings, message: string) => void;
}) {
    const [setup, setSetup] = useState<TestStep[]>(() => hookStepsOf(settings, 'setup_steps'));
    const [teardown, setTeardown] = useState<TestStep[]>(() => hookStepsOf(settings, 'teardown_steps'));
    const [seed, setSeed] = useState(String(settings?.data_seed ?? ''));
    const [error, setError] = useState<string | null>(null);
    const [dirty, setDirty] = useState(false);

    const edit = (key: HookKey) => (next: TestStep[]) => {
        (key === 'setup_steps' ? setSetup : setTeardown)(next);
        setDirty(true);
        setError(null);
    };
    const save = () => {
        for (const [name, list] of [['Setup', setup], ['Teardown', teardown]] as const) {
            const missing = firstUnchosen(list);
            if (missing !== -1) {
                setError(`${name} step ${missing + 1} has no action yet — choose one from its dropdown`);
                return;
            }
        }
        onSave(withHooks(settings, setup, teardown, seed), 'Setup & teardown saved');
        setDirty(false);
    };

    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
            <div className="p-6 lg:p-8 space-y-6">
                <div className="space-y-1">
                    <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                        <ListChecks className="h-4 w-4 text-indigo-500" />
                        Setup &amp; teardown
                    </h3>
                    <p className="text-sm text-slate-500">
                        Setup steps run before this suite's cases and teardown steps after them — around every case in
                        separate and parallel mode, once per job when a sub-suite runs continuously. Teardown always runs,
                        even after a failure; a teardown failure is shown as a warning and does not fail the test. Values a
                        setup step extracts are available to cases as <code>{'{{setup.NAME}}'}</code>.
                        {inherit
                            ? " Parent modules' setup runs first and their teardown last."
                            : ' This module does not inherit settings, so parent modules’ setup and teardown do not run.'}
                    </p>
                </div>

                <HookStepList label="Setup" steps={setup} onChange={edit('setup_steps')} />
                <HookStepList label="Teardown" steps={teardown} onChange={edit('teardown_steps')} />

                <label className="block max-w-md text-sm font-semibold text-slate-700">
                    Data seed
                    <Input
                        value={seed}
                        onChange={(e) => { setSeed(e.target.value); setDirty(true); }}
                        maxLength={64}
                        placeholder="Leave blank for random data"
                        className="mt-1 h-11 rounded-xl"
                    />
                    <span className="mt-1 block text-xs font-normal text-slate-500">
                        Makes <code>{'{{fake.*}}'}</code> values repeatable: the same seed, case and data row always produce the
                        same values. Each run records the seed it used, so a failure can be re-run with identical data.
                    </span>
                    {!seed.trim() && inherit && effectiveSeed && (
                        <span className="mt-1 block text-xs font-normal text-indigo-600">
                            Inheriting seed <code>{effectiveSeed}</code> from a parent module.
                        </span>
                    )}
                </label>

                {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}
                <div className="flex justify-end">
                    <Button type="button" onClick={save} disabled={!dirty}>Save setup &amp; teardown</Button>
                </div>
            </div>
        </div>
    );
}
