// Pure helpers for the accessibility register page (WP15d).
import type { Tone } from '@/components/redesign/kinds';
import type { A11yDay, A11yViolation, A11yWaiver, Impact, WaiverInput } from '@/api/accessibility';

export const IMPACT_COLOUR: Record<Impact, string> = {
    critical: '#B42318',
    serious: '#E0603A',
    moderate: '#F2C94C',
    minor: '#B9B4A8',
};

export const IMPACT_TONE: Record<Impact, Tone> = {
    critical: 'pink', serious: 'peach', moderate: 'butter', minor: 'neutral',
};

export const MAX_WAIVER_DAYS = 90;
const RULE_RE = /^[a-z0-9][a-z0-9-]{0,79}$/;

/** Stack order bottom → top: the worst impact sits on the baseline. */
export const STACK_ORDER: Impact[] = ['critical', 'serious', 'moderate', 'minor'];

export interface Segment { impact: Impact; y0: number; y1: number }

/** Per-day stacked segments (in counts, not pixels). */
export function stackDay(day: A11yDay): Segment[] {
    let y = 0;
    return STACK_ORDER.map((impact) => {
        const seg = { impact, y0: y, y1: y + (day[impact] || 0) };
        y = seg.y1;
        return seg;
    });
}

export function dayTotal(day: A11yDay): number {
    return STACK_ORDER.reduce((n, i) => n + (day[i] || 0), 0);
}

/** Whole days until expiry (negative once expired). */
export function daysLeft(expiresAt: string, now: Date = new Date()): number {
    return Math.ceil((new Date(expiresAt).getTime() - now.getTime()) / 86_400_000);
}

export function waiverLabel(w: Pick<A11yWaiver, 'state' | 'expires_at'>, now: Date = new Date()): string {
    if (w.state === 'expired') return 'Expired — counts again';
    const left = daysLeft(w.expires_at, now);
    return left <= 1 ? 'Expires within a day' : `${left} days left`;
}

/** A waiver form pre-filled from an open violation: this rule, this target. */
export function waiverDraftFor(v: Pick<A11yViolation, 'rule_id' | 'target'>): WaiverInput {
    return { rule_id: v.rule_id, selector_pattern: v.target || '*', reason: '', days: 30 };
}

/** '' when valid, else what to fix. Mirrors the backend's checks. */
export function validateWaiver(w: WaiverInput): string {
    if (!RULE_RE.test((w.rule_id || '').trim().toLowerCase())) return 'Rule must be an axe rule id, e.g. color-contrast.';
    if (!w.reason.trim()) return 'Say why this is accepted for now.';
    if ((w.selector_pattern || '').length > 300) return 'Selector pattern is limited to 300 characters.';
    const d = w.days ?? 30;
    if (!Number.isInteger(d) || d < 1 || d > MAX_WAIVER_DAYS) return `Length must be 1-${MAX_WAIVER_DAYS} days.`;
    return '';
}

export function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
