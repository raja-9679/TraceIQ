import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { kinds, toneClasses, type Kind, type Tone } from './kinds';

// Building blocks for Direction A screens: a list on the left (grouped rows
// that lift into a white card when selected) and one large detail card on the
// right. Colour lives only in small chips and labels; cards stay white.

export function KindChip({ kind, size = 'md' }: { kind: Kind; size?: 'sm' | 'md' }) {
    const k = kinds[kind];
    const Icon = k.icon;
    return (
        <span
            className={cn(
                'flex flex-none items-center justify-center',
                size === 'sm' ? 'h-7 w-7 rounded-[9px]' : 'h-8 w-8 rounded-[10px]',
                toneClasses[k.tone].chip
            )}
            aria-hidden="true"
        >
            <Icon className="h-[15px] w-[15px]" strokeWidth={2.2} />
        </span>
    );
}

export function Pill({ tone, children, className }: { tone: Tone; children: ReactNode; className?: string }) {
    return (
        <span className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold', toneClasses[tone].chip, className)}>
            {children}
        </span>
    );
}

export function PageHeader({
    title, crumbs, subtitle, badge, actions,
}: {
    title: ReactNode;
    crumbs?: ReactNode;
    subtitle?: ReactNode;
    badge?: ReactNode;
    actions?: ReactNode;
}) {
    return (
        <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="flex min-w-0 flex-col gap-1.5">
                {crumbs && <nav aria-label="Breadcrumb" className="flex flex-wrap gap-1.5 text-[13px] text-ink-soft">{crumbs}</nav>}
                <div className="flex flex-wrap items-center gap-3">
                    <h1 className="text-[28px] font-bold leading-tight tracking-[-0.03em] text-ink">{title}</h1>
                    {badge}
                </div>
                {subtitle && <p className="text-sm text-ink-soft">{subtitle}</p>}
            </div>
            {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
    );
}

export function SplitView({ list, detail }: { list: ReactNode; detail: ReactNode }) {
    return (
        <div className="flex flex-wrap items-start gap-5">
            <section className="flex min-w-0 max-w-[440px] flex-[1_1_360px] flex-col gap-[18px]">{list}</section>
            <div className="min-w-0 flex-[999_1_460px]">{detail}</div>
        </div>
    );
}

export function ListGroup({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
    return (
        <div className="flex flex-col gap-1.5">
            <h2 className="px-1 text-xs font-semibold uppercase tracking-[0.06em] text-ink-soft">
                {title}{count !== undefined && ` · ${count}`}
            </h2>
            <div className="flex flex-col gap-0.5">{children}</div>
        </div>
    );
}

export function ListRow({
    selected, onSelect, leading, title, meta, trailing,
}: {
    selected: boolean;
    onSelect: () => void;
    leading?: ReactNode;
    title: ReactNode;
    meta?: ReactNode;
    trailing?: ReactNode;
}) {
    return (
        <button
            type="button"
            onClick={onSelect}
            aria-pressed={selected}
            className={cn(
                'flex min-h-[60px] w-full items-center gap-3 rounded-2xl border px-3 py-2.5 text-left text-ink transition-colors',
                selected
                    ? 'border-border bg-white shadow-[0_4px_16px_rgba(28,27,34,0.07)]'
                    : 'border-transparent hover:bg-white/60'
            )}
        >
            {leading}
            <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="text-sm font-semibold leading-snug">{title}</span>
                {meta && <span className="text-xs text-ink-soft">{meta}</span>}
            </span>
            {trailing}
        </button>
    );
}

export function DetailCard({ children, label }: { children: ReactNode; label: string }) {
    return (
        <article
            aria-label={label}
            className="flex flex-col gap-5 rounded-3xl border border-border bg-white p-6 shadow-[0_12px_40px_rgba(28,27,34,0.06)] sm:p-7"
        >
            {children}
        </article>
    );
}

export function Eyebrow({ children, right }: { children: ReactNode; right?: ReactNode }) {
    return (
        <span className="flex justify-between gap-2 text-xs font-semibold uppercase tracking-[0.04em] text-ink-soft">
            <span>{children}</span>
            {right && <span className="normal-case tracking-normal">{right}</span>}
        </span>
    );
}

export function Panel({ title, right, children, callout = false }: { title: string; right?: ReactNode; children: ReactNode; callout?: boolean }) {
    return (
        <div className={cn('flex flex-col gap-2 rounded-2xl p-4', callout ? 'bg-tiq-callout border border-white/90' : 'bg-background')}>
            <Eyebrow right={right}>{title}</Eyebrow>
            <div className="text-[15px] leading-relaxed text-ink">{children}</div>
        </div>
    );
}

export function EvidenceList({ items }: { items: { tag: string; text: ReactNode; to?: string }[] }) {
    if (items.length === 0) return null;
    return (
        <div className="flex flex-col gap-2">
            <Eyebrow>Evidence</Eyebrow>
            <ul className="overflow-hidden rounded-2xl border border-border">
                {items.map((e, i) => {
                    const body = (
                        <>
                            <span className="min-w-16 font-mono text-xs text-ink-soft">{e.tag}</span>
                            <span className="min-w-0 flex-1 break-words">{e.text}</span>
                        </>
                    );
                    return (
                        <li key={i} className={cn(i > 0 && 'border-t border-[#F0EEE8]')}>
                            {e.to ? (
                                <Link to={e.to} className="flex min-h-12 items-center gap-3 px-4 py-3 text-sm hover:bg-background">{body}</Link>
                            ) : (
                                <div className="flex items-center gap-3 px-4 py-3 text-sm">{body}</div>
                            )}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
    return (
        <div className="flex flex-col items-center gap-2 rounded-3xl border border-border bg-white px-6 py-12 text-center">
            <p className="text-lg font-semibold text-ink">{title}</p>
            {children && <p className="max-w-[48ch] text-sm text-ink-soft">{children}</p>}
        </div>
    );
}
