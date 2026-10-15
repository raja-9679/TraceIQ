import { useState } from 'react';
import { MonitorSmartphone } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
    Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
    COLOR_SCHEMES, EMPTY_MATRIX, MatrixChoice, MatrixSettings, VIEWPORT_PRESET_NAMES, inheritedSummary,
    isRtlLocale, matrixFromSettings, parseLocales, settingsWithMatrix, toggle, variantCount,
} from './matrixHelpers';

const VIEWPORT_SIZES: Record<string, string> = {
    mobile: '375×812', tablet: '768×1024', desktop: '1366×768', wide: '1920×1080',
};

/** The viewport / locale / colour-scheme / reduced-motion controls. */
export function MatrixFields({ value, onChange, idPrefix }: {
    value: MatrixChoice;
    onChange: (next: MatrixChoice) => void;
    idPrefix: string;
}) {
    // Local text while typing; parsed into tags on blur.
    const [localeText, setLocaleText] = useState(value.locales.join(', '));
    const parsed = parseLocales(localeText);

    return (
        <div className="space-y-5">
            <fieldset className="space-y-2">
                <legend className="text-xs font-bold uppercase tracking-wide text-slate-400">Screen widths</legend>
                <div className="grid grid-cols-2 gap-2">
                    {VIEWPORT_PRESET_NAMES.map((vp) => {
                        const id = `${idPrefix}-vp-${vp}`;
                        return (
                            <label key={vp} htmlFor={id} className="flex items-center gap-2.5 text-sm font-semibold text-slate-700 cursor-pointer bg-slate-50/50 border border-slate-200 rounded-xl px-3 h-10">
                                <input id={id} type="checkbox" className="rounded accent-indigo-600"
                                    checked={value.viewports.includes(vp)}
                                    onChange={(e) => onChange({ ...value, viewports: toggle(value.viewports, vp, e.target.checked) })} />
                                <span className="capitalize">{vp}</span>
                                <span className="text-xs font-normal text-slate-400 ml-auto">{VIEWPORT_SIZES[vp]}</span>
                            </label>
                        );
                    })}
                </div>
            </fieldset>
            <div className="space-y-1.5">
                <label htmlFor={`${idPrefix}-locales`} className="text-xs font-bold uppercase tracking-wide text-slate-400">
                    Locales (BCP 47, comma separated)
                </label>
                <Input id={`${idPrefix}-locales`} value={localeText} placeholder="en-US, ar-EG"
                    onChange={(e) => setLocaleText(e.target.value)}
                    onBlur={() => onChange({ ...value, locales: parseLocales(localeText).locales })} />
                {parsed.invalid.length > 0 && (
                    <p className="text-xs text-rose-600">Not a locale tag: {parsed.invalid.join(', ')}</p>
                )}
                {parsed.locales.some(isRtlLocale) && (
                    <p className="text-xs text-slate-500">Right-to-left locale included — add an <code>expect-rtl-layout</code> step to check mirroring.</p>
                )}
            </div>
            <fieldset className="space-y-2">
                <legend className="text-xs font-bold uppercase tracking-wide text-slate-400">Colour scheme</legend>
                <div className="flex gap-2">
                    {COLOR_SCHEMES.map((s) => {
                        const id = `${idPrefix}-scheme-${s}`;
                        return (
                            <label key={s} htmlFor={id} className="flex items-center gap-2 text-sm font-semibold text-slate-700 cursor-pointer bg-slate-50/50 border border-slate-200 rounded-xl px-3 h-10">
                                <input id={id} type="checkbox" className="rounded accent-indigo-600"
                                    checked={value.color_schemes.includes(s)}
                                    onChange={(e) => onChange({ ...value, color_schemes: toggle(value.color_schemes, s, e.target.checked) })} />
                                <span className="capitalize">{s}</span>
                            </label>
                        );
                    })}
                    <label htmlFor={`${idPrefix}-rm`} className="flex items-center gap-2 text-sm font-semibold text-slate-700 cursor-pointer bg-slate-50/50 border border-slate-200 rounded-xl px-3 h-10">
                        <input id={`${idPrefix}-rm`} type="checkbox" className="rounded accent-indigo-600"
                            checked={value.reduced_motion}
                            onChange={(e) => onChange({ ...value, reduced_motion: e.target.checked })} />
                        Reduced motion
                    </label>
                </div>
            </fieldset>
        </div>
    );
}

/** Run-time picker next to the Run button: overrides the suite's matrix for this run. */
export function RunMatrixPicker({ value, onChange }: { value: MatrixChoice; onChange: (v: MatrixChoice) => void }) {
    const [open, setOpen] = useState(false);
    const [draft, setDraft] = useState<MatrixChoice>(value);
    const n = variantCount(value);
    const active = n > 1 || value.color_schemes.length > 0 || value.locales.length > 0 || value.viewports.length > 0 || value.reduced_motion;
    return (
        <>
            <Button type="button" variant="outline" onClick={() => { setDraft(value); setOpen(true); }}
                className="rounded-xl border-slate-200 text-slate-700 bg-white hover:bg-slate-50 shadow-sm h-11 px-4 font-medium">
                <MonitorSmartphone className="mr-2 h-4 w-4 text-indigo-500" />
                {active ? `Sweep: ${n} variant${n === 1 ? '' : 's'}` : 'Screen sizes & locales'}
            </Button>
            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Frontend matrix for the next run</DialogTitle>
                        <DialogDescription>
                            Each combination runs as its own run (like browsers × devices) and the run page shows them
                            side by side. Leave everything empty to use the suite's settings.
                        </DialogDescription>
                    </DialogHeader>
                    <MatrixFields value={draft} onChange={setDraft} idPrefix="run-matrix" />
                    <DialogFooter className="gap-2">
                        <Button type="button" variant="outline" onClick={() => { onChange(EMPTY_MATRIX); setOpen(false); }}>Clear</Button>
                        <Button type="button" onClick={() => { onChange(draft); setOpen(false); }}>
                            Use {variantCount(draft)} variant{variantCount(draft) === 1 ? '' : 's'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </>
    );
}

/** Suite settings card: the matrix every run of this module (and its children) uses. */
export function FrontendMatrixSettings({ settings, effective, onSave }: {
    settings: MatrixSettings | null | undefined;
    effective: MatrixSettings | null | undefined;
    onSave: (next: MatrixSettings, message: string) => void;
}) {
    const current = matrixFromSettings(settings);
    const inherited = inheritedSummary(settings, effective);
    const save = (next: MatrixChoice) =>
        onSave(settingsWithMatrix(settings, next), `Frontend matrix: ${variantCount(next)} variant(s) per run`);
    return (
        <div className="bg-white rounded-3xl border border-slate-200 shadow-sm overflow-hidden hover:border-slate-300 transition-colors">
            <div className="p-6 lg:p-8 space-y-5">
                <div className="space-y-1">
                    <h3 className="text-base font-bold text-slate-900 flex items-center gap-2">
                        <MonitorSmartphone className="h-4 w-4 text-indigo-500" />
                        Screen sizes, locales &amp; themes
                    </h3>
                    <p className="text-sm text-slate-500">
                        Fan every run out over screen widths, locales and colour schemes. Each combination is its own run;
                        the run page lines their per-step screenshots up side by side. Visual baselines are kept per combination.
                    </p>
                    {inherited && (
                        <p className="text-xs font-semibold text-indigo-600">Inherited: {inherited}</p>
                    )}
                </div>
                <MatrixFields value={current} onChange={save} idPrefix="suite-matrix" />
            </div>
        </div>
    );
}
