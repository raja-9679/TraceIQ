import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Form idiom for the Settings screens (Direction A): one white card per topic,
// rows with the label on the left and the control on the right, and the
// card's Save action at the bottom-left. Mirrors QualityDashboard's
// NumberRow/CheckRow so the two screens read the same.

export const inputClass = 'h-10 rounded-full';
export const textareaClass =
    'w-full rounded-xl border border-input bg-transparent p-3 font-mono text-sm text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring';
export const checkboxClass = 'h-[18px] w-[18px] flex-none cursor-pointer accent-ink';
/** White callout with the 4px butter bar on the left (warnings / notes). */
export const warnCalloutClass =
    'rounded-2xl border border-border bg-white px-4 py-3 text-sm text-ink shadow-[inset_4px_0_0_#F2C94C]';

export function SettingsCard({
    title, description, icon, actions, footer, danger = false, children, className, labelledBy,
}: {
    title: ReactNode;
    description?: ReactNode;
    icon?: ReactNode;
    /** Right side of the heading (e.g. an "Add" button). */
    actions?: ReactNode;
    /** Bottom row: the card's Save button goes first so it lands bottom-left. */
    footer?: ReactNode;
    /** Visually separated card for destructive actions. */
    danger?: boolean;
    children?: ReactNode;
    className?: string;
    labelledBy?: string;
}) {
    return (
        <section
            aria-labelledby={labelledBy}
            className={cn(
                'flex min-w-0 flex-col gap-4 rounded-3xl border bg-white p-6',
                danger ? 'border-destructive/40' : 'border-border',
                className,
            )}
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                    <h2 id={labelledBy} className="flex items-center gap-2 text-base font-semibold text-ink">
                        {icon}
                        {title}
                    </h2>
                    {description && <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">{description}</p>}
                </div>
                {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
            </div>
            {children}
            {footer && <div className="flex flex-wrap items-center gap-3 border-t border-border pt-4">{footer}</div>}
        </section>
    );
}

/** Label on the left, control on the right. Stacks on phones. */
export function FieldRow({
    label, htmlFor, hint, badges, children, wide = false,
}: {
    label: ReactNode;
    htmlFor?: string;
    hint?: ReactNode;
    badges?: ReactNode;
    children: ReactNode;
    /** Give the control more room (long URLs, text inputs). */
    wide?: boolean;
}) {
    return (
        <div className="flex min-h-11 flex-col gap-2 py-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <div className="min-w-0 sm:flex-1">
                <div className="flex flex-wrap items-center gap-2">
                    {htmlFor ? (
                        <label htmlFor={htmlFor} className="text-sm font-medium text-ink">{label}</label>
                    ) : (
                        <span className="text-sm font-medium text-ink">{label}</span>
                    )}
                    {badges}
                </div>
                {hint && <p className="mt-0.5 text-xs leading-relaxed text-ink-soft">{hint}</p>}
            </div>
            <div className={cn('flex min-w-0 items-center gap-2', wide ? 'sm:w-[min(100%,24rem)]' : 'sm:w-64')}>
                {children}
            </div>
        </div>
    );
}

/** A whole-row checkbox: clicking the text toggles it. */
export function CheckRow({
    label, hint, checked, onChange, disabled, id,
}: {
    label: ReactNode;
    hint?: ReactNode;
    checked: boolean;
    onChange: (v: boolean) => void;
    disabled?: boolean;
    id?: string;
}) {
    return (
        <label
            htmlFor={id}
            className={cn('flex min-h-11 items-center justify-between gap-4 py-1 text-sm', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}
        >
            <span className="min-w-0">
                <span className="block font-medium text-ink">{label}</span>
                {hint && <span className="mt-0.5 block text-xs leading-relaxed text-ink-soft">{hint}</span>}
            </span>
            <input
                id={id}
                type="checkbox"
                className={checkboxClass}
                checked={checked}
                disabled={disabled}
                onChange={(e) => onChange(e.target.checked)}
            />
        </label>
    );
}

/** Thin divider + optional sub-heading between groups of rows inside a card. */
export function RowGroup({ title, children }: { title?: ReactNode; children: ReactNode }) {
    return (
        <div className="flex flex-col border-t border-border pt-3">
            {title && <h3 className="mb-1 text-sm font-semibold text-ink">{title}</h3>}
            {children}
        </div>
    );
}

/** Vertical section list on wide screens, a labelled select on phones. */
export function SectionNav<T extends string>({
    sections, active, onChange, label,
}: {
    sections: { id: T; name: string }[];
    active: T;
    onChange: (id: T) => void;
    label: string;
}) {
    return (
        <>
            <div className="lg:hidden">
                <label htmlFor="settings-section-select" className="mb-1.5 block text-sm font-medium text-ink">Section</label>
                <select
                    id="settings-section-select"
                    value={active}
                    onChange={(e) => onChange(e.target.value as T)}
                    className="h-11 w-full rounded-full border border-input bg-white px-4 text-sm text-ink focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                >
                    {sections.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
            </div>
            <nav aria-label={label} className="hidden rounded-3xl border border-border bg-white p-2 lg:block">
                <ul className="flex flex-col gap-0.5">
                    {sections.map((s) => {
                        const isActive = s.id === active;
                        return (
                            <li key={s.id}>
                                <button
                                    type="button"
                                    onClick={() => onChange(s.id)}
                                    aria-current={isActive ? 'page' : undefined}
                                    className={cn(
                                        'flex min-h-11 w-full items-center rounded-full px-4 text-left text-sm transition-colors',
                                        isActive
                                            ? 'bg-white font-semibold text-ink shadow-[0_2px_10px_rgba(28,27,34,0.08)]'
                                            : 'font-medium text-ink-soft hover:bg-white/60 hover:text-ink',
                                    )}
                                >
                                    {s.name}
                                </button>
                            </li>
                        );
                    })}
                </ul>
            </nav>
        </>
    );
}
