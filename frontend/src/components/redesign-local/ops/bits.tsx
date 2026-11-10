import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

// Small Direction A pieces shared by the operations pages
// (Worker fleet, On-call, Access reviews).

/** A white panel with its title, a one-line explanation and actions inside it. */
export function SectionPanel({ title, description, actions, children, footer, flush = false }: {
    title: ReactNode;
    description?: ReactNode;
    actions?: ReactNode;
    children: ReactNode;
    footer?: ReactNode;
    /** Body without padding (for tables that run edge to edge). */
    flush?: boolean;
}) {
    return (
        <section className="overflow-hidden rounded-3xl border border-border bg-white">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[#F0EEE8] px-5 py-4 sm:px-6">
                <div className="flex min-w-0 flex-col gap-1">
                    <h2 className="text-base font-semibold text-ink">{title}</h2>
                    {description && <p className="max-w-[70ch] text-[13px] leading-relaxed text-ink-soft">{description}</p>}
                </div>
                {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
            </div>
            <div className={cn(!flush && 'px-5 py-5 sm:px-6')}>{children}</div>
            {footer && <div className="border-t border-[#F0EEE8] px-5 py-3 text-xs text-ink-soft sm:px-6">{footer}</div>}
        </section>
    );
}

/** One labelled value in a detail card's fact grid. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
    return (
        <div className="flex min-w-0 flex-col gap-1 rounded-2xl bg-background px-4 py-3">
            <dt className="text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">{label}</dt>
            <dd className="min-w-0 break-words text-sm text-ink">{children}</dd>
        </div>
    );
}

export function FactGrid({ children }: { children: ReactNode }) {
    return <dl className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-2">{children}</dl>;
}

/** Message shown inside a panel when there is nothing (or an error) to list. */
export function PanelNote({ children, tone = 'soft' }: { children: ReactNode; tone?: 'soft' | 'error' }) {
    return (
        <p className={cn('px-5 py-8 text-center text-sm sm:px-6', tone === 'error' ? 'text-pastel-ink-peach' : 'text-ink-soft')}>
            {children}
        </p>
    );
}
