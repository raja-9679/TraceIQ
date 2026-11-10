// The "Get started" card: wording and destinations for each step the backend
// reports (GET /projects/{id}/getting-started). The backend decides what is
// done, from real data; this file only says how to present it.

export type StepKey = 'test' | 'run' | 'passed' | 'schedule' | 'team';

export interface GettingStartedData {
    project_id: number;
    steps: { key: string; done: boolean }[];
    done: number;
    total: number;
    complete: boolean;
    first_suite_id?: number | null;
    latest_run_id?: number | null;
}

export interface StepView {
    key: StepKey;
    done: boolean;
    /** The first step not yet done: the one to do now. */
    current: boolean;
    title: string;
    text: string;
    action: string;
    to: string;
}

const COPY: Record<StepKey, { title: string; text: string; action: string }> = {
    test: {
        title: 'Create your first test',
        text: 'Open a suite, press New test, then press Record and use your site while TraceIQ writes the steps. You can also add steps by hand.',
        action: 'Go to suites',
    },
    run: {
        title: 'Run it',
        text: 'Open the suite and press Run now. A run takes a browser through the steps and keeps what it saw.',
        action: 'Go to suites',
    },
    passed: {
        title: 'See it pass',
        text: 'Open the run. If a step failed, the run page shows the step, the error and a likely cause; fix the test or the site and run again.',
        action: 'Open runs',
    },
    schedule: {
        title: 'Run it automatically',
        text: 'Add a schedule so the suite runs every night or every hour without anyone pressing a button.',
        action: 'Add a schedule',
    },
    team: {
        title: 'Invite a teammate',
        text: 'Give a colleague access to this workspace. They get a link to create their account, by email when email is set up.',
        action: 'Invite people',
    },
};

const isStepKey = (key: string): key is StepKey => key in COPY;

function destination(key: StepKey, data: GettingStartedData): string {
    switch (key) {
        case 'test':
            // Straight into the editor when there is a suite to put the test in.
            return data.first_suite_id ? `/suites/${data.first_suite_id}/builder` : '/suites';
        case 'run':
            return data.first_suite_id ? `/suites/${data.first_suite_id}` : '/suites';
        case 'passed':
            return data.latest_run_id ? `/runs/${data.latest_run_id}` : '/runs';
        case 'schedule':
            return '/schedules';
        case 'team':
            return '/workspace';
    }
}

/** Steps in the order the backend lists them; unknown keys (a newer backend) are skipped. */
export function stepViews(data: GettingStartedData): StepView[] {
    const known = data.steps.filter((s): s is { key: StepKey; done: boolean } => isStepKey(s.key));
    const firstOpen = known.findIndex((s) => !s.done);
    return known.map((s, i) => {
        const copy = COPY[s.key];
        const action = s.key === 'test' && data.first_suite_id ? 'Create a test' : copy.action;
        return { key: s.key, done: s.done, current: i === firstOpen, title: copy.title, text: copy.text, action, to: destination(s.key, data) };
    });
}

const HIDDEN_KEY = (projectId: number) => `tiq_getting_started_hidden_${projectId}`;

/** Hiding is a per-browser choice; storage may be unavailable (private window). */
export function isHidden(projectId: number): boolean {
    try { return localStorage.getItem(HIDDEN_KEY(projectId)) === '1'; } catch { return false; }
}
export function setHidden(projectId: number, hidden: boolean): void {
    try {
        if (hidden) localStorage.setItem(HIDDEN_KEY(projectId), '1');
        else localStorage.removeItem(HIDDEN_KEY(projectId));
    } catch { /* the card simply comes back next time */ }
}
