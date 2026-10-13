import type { Kind, Tone } from '@/components/redesign/kinds';

// Run / result status -> how it is drawn. Statuses come from the backend in
// either case ("passed" or "PASSED"), so normalise first.
export const statusLook = (status: string | null | undefined): { kind: Kind; tone: Tone; label: string } => {
    switch ((status ?? '').toLowerCase()) {
        case 'passed': return { kind: 'pass', tone: 'mint', label: 'Passed' };
        case 'failed': return { kind: 'failure', tone: 'peach', label: 'Failed' };
        case 'error': return { kind: 'block', tone: 'butter', label: 'Error' };
        case 'running': return { kind: 'review', tone: 'lavender', label: 'Running' };
        case 'pending': return { kind: 'skip', tone: 'neutral', label: 'Queued' };
        case 'skipped': return { kind: 'skip', tone: 'neutral', label: 'Skipped' };
        default: return { kind: 'skip', tone: 'neutral', label: status || 'Unknown' };
    }
};

export const BAR = { pass: '#8FD3B6', fail: '#E0603A', warn: '#F2C94C' } as const;
