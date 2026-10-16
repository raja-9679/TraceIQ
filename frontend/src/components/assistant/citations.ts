import type { AnswerStatus, Citation } from '@/api/assistant';

/** Where a cited record lives in the app. Pages that are per project (triage,
 * security, readiness, quality) show the active project, so the drawer
 * switches to the citation's project before navigating. */
export function citationHref(c: Citation): string {
    switch (c.kind) {
        case 'run':
            return `/runs/${c.id}`;
        case 'result':
            return c.run_id ? `/runs/${c.run_id}` : '/runs';
        case 'case':
            return c.suite_id ? `/suites/${c.suite_id}/cases/${c.id}/edit` : '/suites';
        case 'cluster':
            return '/triage';
        case 'finding':
            return '/security';
        case 'readiness':
            return '/release-readiness';
        case 'project':
            return '/quality';
        default:
            return '/';
    }
}

const KIND_LABEL: Record<Citation['kind'], string> = {
    project: 'Project', run: 'Run', result: 'Result', cluster: 'Failure cluster',
    finding: 'Finding', case: 'Test case', readiness: 'Readiness',
};

export function citationLabel(c: Citation): string {
    return c.label || `${KIND_LABEL[c.kind] ?? 'Record'} #${c.id}`;
}

export function citationKindLabel(c: Citation): string {
    return KIND_LABEL[c.kind] ?? 'Record';
}

/** Answers that are an explanation from TraceIQ, not from the model. */
export function isSystemNotice(status: AnswerStatus | null): boolean {
    return status !== null && status !== 'answered';
}

/** Split answer text so inline [kind:id] markers can render as chips. */
export function splitInlineRefs(text: string): { text: string; ref?: string }[] {
    const out: { text: string; ref?: string }[] = [];
    const re = /\[([a-z]+:\d{1,12})\]/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
        if (m.index > last) out.push({ text: text.slice(last, m.index) });
        out.push({ text: m[0], ref: m[1] });
        last = m.index + m[0].length;
    }
    if (last < text.length) out.push({ text: text.slice(last) });
    return out;
}

export const SUGGESTIONS = [
    'Why did the last failed run fail?',
    'Which tests are flaky in this project?',
    'Is the release ready?',
    'What are the open failure clusters?',
];

/** Backend datetimes are naive UTC; mark them as UTC unless already zoned. */
export function formatWhen(iso: string): string {
    const zoned = /(Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`;
    const d = new Date(zoned);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
