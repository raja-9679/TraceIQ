// Pure helpers for the Estate and Script inventory pages (kept out of the
// component files so fast refresh works and they can be unit-tested).
import { useEffect, useState } from 'react';
import type { ElementCheck, MonitoredPage, MonitoredPageInput } from '@/api/estates';

export function scoreTone(score: number | null | undefined): string {
    if (score === null || score === undefined) return 'bg-slate-100 text-slate-500';
    if (score >= 90) return 'bg-emerald-100 text-emerald-800';
    if (score >= 75) return 'bg-amber-100 text-amber-800';
    return 'bg-rose-100 text-rose-800';
}

export function statusTone(status: string | null | undefined): string {
    switch (status) {
        case 'pass': case 'ok': return 'bg-emerald-100 text-emerald-800';
        case 'warn': case 'changed': return 'bg-amber-100 text-amber-800';
        case 'fail': case 'alert': case 'error': return 'bg-rose-100 text-rose-800';
        default: return 'bg-slate-100 text-slate-600';
    }
}

export function daysLeft(iso: string | null | undefined, now: Date = new Date()): number | null {
    if (!iso) return null;
    // Backend datetimes are naive UTC.
    const t = Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
    if (Number.isNaN(t)) return null;
    return Math.floor((t - now.getTime()) / 86_400_000);
}

export function certTone(days: number | null): string {
    if (days === null) return 'text-slate-400';
    if (days <= 7) return 'text-rose-700 font-semibold';
    if (days <= 30) return 'text-amber-700';
    return 'text-slate-600';
}

/** SVG polyline points for a 0..100 score series in a w×h box. */
export function sparklinePoints(values: number[], w: number, h: number): string {
    if (values.length === 0) return '';
    if (values.length === 1) return `0,${y(values[0], h)} ${w},${y(values[0], h)}`;
    const step = w / (values.length - 1);
    return values.map((v, i) => `${round(i * step)},${y(v, h)}`).join(' ');
}

function y(v: number, h: number): number {
    const c = Math.max(0, Math.min(100, v));
    return round(h - (c / 100) * h);
}

function round(n: number): number {
    return Math.round(n * 10) / 10;
}

export function splitList(text: string): string[] {
    return text.split(/[\n,]/).map(s => s.trim()).filter(Boolean);
}

/** "selector | must contain | forbidden1; forbidden2" per line. */
export function parseElementChecks(text: string): ElementCheck[] {
    return text.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
        const [selector, must, forbidden] = line.split('|').map(s => (s ?? '').trim());
        return {
            selector,
            must_contain: must || null,
            must_not_contain: (forbidden || '').split(';').map(s => s.trim()).filter(Boolean),
        };
    }).filter(c => c.selector);
}

export function formatElementChecks(checks: ElementCheck[] | null | undefined): string {
    return (checks || []).map(c => {
        const forbidden = (c.must_not_contain || []).join('; ');
        if (!c.must_contain && !forbidden) return c.selector;
        return [c.selector, c.must_contain || '', forbidden].join(' | ').replace(/\s\|\s$/, '');
    }).join('\n');
}

export const EMPTY_PAGE: MonitoredPageInput = {
    name: '', url: '', track_scripts: true, track_content: false,
    cron_expression: '0 6 * * 1', schedule_enabled: true, similarity_threshold: 0.5,
    forbidden_keywords: [], element_checks: [], alert_emails: [],
};

export function pageInput(p: MonitoredPage | null): MonitoredPageInput {
    if (!p) return { ...EMPTY_PAGE };
    return {
        name: p.name, url: p.url, track_scripts: p.track_scripts, track_content: p.track_content,
        cron_expression: p.cron_expression, schedule_enabled: p.schedule_enabled,
        similarity_threshold: p.similarity_threshold, forbidden_keywords: p.forbidden_keywords || [],
        element_checks: p.element_checks || [], alert_emails: p.alert_emails || [],
    };
}

export const MIN_JUSTIFICATION = 10;

export function justificationProblem(text: string): string | null {
    const t = text.trim();
    if (!t) return 'A justification is required (PCI DSS 6.4.3).';
    if (t.length < MIN_JUSTIFICATION) return `Write at least ${MIN_JUSTIFICATION} characters.`;
    return null;
}

export function shortHash(h: string | null | undefined): string {
    if (!h) return '—';
    return h.length > 22 ? `${h.slice(0, 22)}…` : h;
}

/** The project picked in the sidebar, kept in sync across pages. */
export function useActiveProject(): [number | null, (id: number) => void] {
    const read = () => { const s = localStorage.getItem('activeProjectId'); return s ? parseInt(s) : null; };
    const [projectId, setProjectId] = useState<number | null>(read);
    useEffect(() => {
        const h = () => setProjectId(read());
        window.addEventListener('projectChanged', h);
        return () => window.removeEventListener('projectChanged', h);
    }, []);
    const select = (id: number) => {
        setProjectId(id);
        localStorage.setItem('activeProjectId', id.toString());
        window.dispatchEvent(new Event('projectChanged'));
    };
    return [projectId, select];
}
