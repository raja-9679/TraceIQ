import { useState } from 'react';
import { CalendarCheck } from 'lucide-react';
import { intervalFromInput } from './ownershipHelpers';

// Suite setting `review_interval_days` (WP16b): how often the tests in this
// module must be reviewed by a person. Empty = inherit the parent module's
// value (default 90 days); 0 = no periodic review here.

type Settings = Record<string, unknown>;

export function ReviewIntervalCard({ settings, effective, inherit, onSave }: {
    settings: Settings | null | undefined;
    effective: Settings | null | undefined;
    inherit: boolean;
    onSave: (next: Settings, message: string) => void;
}) {
    const own = settings || {};
    const ownValue = typeof own.review_interval_days === 'number' ? own.review_interval_days : null;
    const [text, setText] = useState(ownValue === null ? '' : String(ownValue));
    const [invalid, setInvalid] = useState(false);
    const inherited = typeof effective?.review_interval_days === 'number' ? effective.review_interval_days as number : null;

    const commit = () => {
        const v = intervalFromInput(text);
        if (v === 'invalid') { setInvalid(true); return; }
        setInvalid(false);
        if (v === ownValue) return;
        const next: Settings = { ...own };
        if (v === null) delete next.review_interval_days;
        else next.review_interval_days = v;
        onSave(next, v === null ? 'Review interval inherited' : v === 0 ? 'Periodic review turned off for this module'
            : `Tests here need a review every ${v} days`);
    };

    const placeholder = inherit && inherited !== null ? `Inherited: ${inherited}` : 'Default: 90';

    return (
        <div className="overflow-hidden rounded-3xl border border-border bg-white">
            <div className="flex flex-col gap-4 p-6 lg:p-8">
                <div className="flex flex-col gap-1">
                    <h3 className="flex items-center gap-2 text-base font-bold text-ink">
                        <CalendarCheck className="h-4 w-4" /> Test review
                    </h3>
                    <p className="text-sm text-ink-soft">
                        How often a person should confirm each test here still checks something worth checking.
                        Tests past their review show on the Test review page. Leave empty to inherit; 0 turns it off.
                    </p>
                </div>
                <label htmlFor="review-interval" className="flex max-w-[260px] flex-col gap-1 text-xs font-semibold text-ink-soft">
                    Review every (days)
                    <input id="review-interval" inputMode="numeric" value={text} placeholder={placeholder}
                        onChange={(e) => setText(e.target.value)} onBlur={commit}
                        onKeyDown={(e) => { if (e.key === 'Enter') commit(); }}
                        aria-invalid={invalid}
                        className="h-11 rounded-xl border border-border bg-white px-3 text-sm font-normal text-ink" />
                    {invalid && <span className="font-normal text-pastel-ink-peach">A whole number of days from 0 to 3650.</span>}
                </label>
            </div>
        </div>
    );
}
