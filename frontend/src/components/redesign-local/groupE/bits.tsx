import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { Tone } from '@/components/redesign/kinds';
import { toneClasses } from '@/components/redesign/kinds';

// Small layout helpers shared by the workspace & access pages (group E).
// They only arrange content; all data and behaviour stay in the pages.

export function Facts({ items }: { items: { label: string; value: ReactNode }[] }) {
    return (
        <dl className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] gap-3">
            {items.map((f) => (
                <div key={f.label} className="flex min-w-0 flex-col gap-1 rounded-2xl bg-background p-4">
                    <dt className="text-xs text-ink-soft">{f.label}</dt>
                    <dd className="min-w-0 break-words text-sm font-semibold text-ink">{f.value}</dd>
                </div>
            ))}
        </dl>
    );
}

const AVATAR_TONES: Tone[] = ['lavender', 'mint', 'peach', 'butter', 'pink'];

export function Avatar({ name, size = 'md' }: { name: string; size?: 'sm' | 'md' }) {
    const clean = (name || '?').trim();
    const parts = clean.split(/[\s@._-]+/).filter(Boolean);
    const initials = ((parts[0]?.[0] ?? '?') + (parts[1]?.[0] ?? '')).toUpperCase();
    let h = 0;
    for (let i = 0; i < clean.length; i++) h = (h * 31 + clean.charCodeAt(i)) >>> 0;
    const tone = AVATAR_TONES[h % AVATAR_TONES.length];
    return (
        <span
            aria-hidden="true"
            className={cn(
                'flex flex-none items-center justify-center rounded-full font-semibold',
                size === 'sm' ? 'h-7 w-7 text-[11px]' : 'h-9 w-9 text-xs',
                toneClasses[tone].chip
            )}
        >
            {initials}
        </span>
    );
}

export function SectionTabs<T extends string>({
    tabs, value, onChange, label,
}: {
    tabs: { id: T; label: string; count?: number }[];
    value: T;
    onChange: (v: T) => void;
    label: string;
}) {
    return (
        <div role="tablist" aria-label={label} className="flex flex-wrap gap-1 self-start rounded-full bg-[#EFEDE7] p-1">
            {tabs.map((t) => (
                <button
                    key={t.id}
                    type="button"
                    role="tab"
                    aria-selected={value === t.id}
                    onClick={() => onChange(t.id)}
                    className={cn(
                        'flex min-h-10 items-center gap-1.5 whitespace-nowrap rounded-full px-4 text-[13px] text-ink',
                        value === t.id ? 'bg-white font-semibold shadow-[0_1px_4px_rgba(28,27,34,0.1)]' : 'font-medium hover:bg-white/50'
                    )}
                >
                    {t.label}
                    {t.count !== undefined && <span className="font-mono text-xs text-ink-soft">{t.count}</span>}
                </button>
            ))}
        </div>
    );
}

export function ListSkeleton({ rows = 3 }: { rows?: number }) {
    return (
        <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading">
            {Array.from({ length: rows }).map((_, i) => (
                <div key={i} className="h-[60px] max-w-[440px] animate-pulse rounded-2xl bg-white/70" />
            ))}
        </div>
    );
}

export function FormCard({ title, subtitle, children, className }: { title: string; subtitle?: ReactNode; children: ReactNode; className?: string }) {
    return (
        <section className={cn('flex flex-col gap-4 rounded-3xl border border-border bg-white p-6', className)}>
            <div className="flex flex-col gap-1">
                <h2 className="text-base font-semibold text-ink">{title}</h2>
                {subtitle && <p className="text-[13px] leading-relaxed text-ink-soft">{subtitle}</p>}
            </div>
            {children}
        </section>
    );
}

export function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
    return (
        <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={id} className="text-[13px] font-semibold text-ink">{label}</label>
            {children}
            {hint && <p className="text-xs text-ink-soft">{hint}</p>}
        </div>
    );
}
