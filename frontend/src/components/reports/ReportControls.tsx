import type { ReactNode } from 'react';

// Labelled filter controls shared by the WP12b report pages' header toolbars.

const selectClass = 'h-10 rounded-full border border-border bg-white px-4 text-sm text-ink';

export function WindowSelect({ id, value, options, onChange, label = 'Window' }: {
    id: string;
    value: number;
    options: number[];
    onChange: (v: number) => void;
    label?: string;
}) {
    return (
        <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-ink">
            {label}
            <select id={id} className={selectClass} value={value} onChange={(e) => onChange(Number(e.target.value))}>
                {options.map((d) => <option key={d} value={d}>Last {d} days</option>)}
            </select>
        </label>
    );
}

export function LabelledSelect({ id, label, value, onChange, children }: {
    id: string;
    label: string;
    value: string;
    onChange: (v: string) => void;
    children: ReactNode;
}) {
    return (
        <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-ink">
            {label}
            <select id={id} className={selectClass} value={value} onChange={(e) => onChange(e.target.value)}>
                {children}
            </select>
        </label>
    );
}

/** A metric tile: label, big value, optional pill and small print. */
export function MetricTile({ label, value, pill, children }: {
    label: string;
    value: ReactNode;
    pill?: ReactNode;
    children?: ReactNode;
}) {
    return (
        <section aria-label={label} className="flex min-w-0 flex-col gap-2 rounded-3xl border border-border bg-white p-5">
            <span className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">{label}</span>
                {pill}
            </span>
            <span className="text-[28px] font-bold leading-tight tracking-tight tabular-nums text-ink">{value}</span>
            {children && <div className="flex flex-col gap-1 text-xs leading-relaxed text-ink-soft">{children}</div>}
        </section>
    );
}
