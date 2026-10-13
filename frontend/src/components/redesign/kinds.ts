import {
    X, Eye, Image, Activity, ShieldAlert, Radio, Check, Minus, AlertCircle, type LucideIcon,
} from 'lucide-react';

export type Tone = 'peach' | 'lavender' | 'pink' | 'butter' | 'mint' | 'neutral';

export const toneClasses: Record<Tone, { chip: string; ink: string }> = {
    peach: { chip: 'bg-pastel-peach text-pastel-ink-peach', ink: 'text-pastel-ink-peach' },
    lavender: { chip: 'bg-pastel-lavender text-pastel-ink-lavender', ink: 'text-pastel-ink-lavender' },
    pink: { chip: 'bg-pastel-pink text-pastel-ink-pink', ink: 'text-pastel-ink-pink' },
    butter: { chip: 'bg-pastel-butter text-pastel-ink-butter', ink: 'text-pastel-ink-butter' },
    mint: { chip: 'bg-pastel-mint text-pastel-ink-mint', ink: 'text-pastel-ink-mint' },
    neutral: { chip: 'bg-secondary text-ink-muted', ink: 'text-ink-muted' },
};

export type Kind = 'failure' | 'review' | 'visual' | 'flaky' | 'security' | 'monitor' | 'pass' | 'skip' | 'block';

export const kinds: Record<Kind, { label: string; tone: Tone; icon: LucideIcon }> = {
    failure: { label: 'Failure', tone: 'peach', icon: X },
    review: { label: 'Review', tone: 'lavender', icon: Eye },
    visual: { label: 'Visual change', tone: 'pink', icon: Image },
    flaky: { label: 'Flaky', tone: 'butter', icon: Activity },
    security: { label: 'Security', tone: 'mint', icon: ShieldAlert },
    monitor: { label: 'Monitor', tone: 'peach', icon: Radio },
    pass: { label: 'Passed', tone: 'mint', icon: Check },
    skip: { label: 'Not reached', tone: 'neutral', icon: Minus },
    block: { label: 'Blocking', tone: 'peach', icon: AlertCircle },
};
