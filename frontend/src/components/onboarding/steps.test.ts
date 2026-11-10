import { describe, expect, it } from 'vitest';
import { stepViews, type GettingStartedData } from './steps';

const data = (over: Partial<GettingStartedData> = {}): GettingStartedData => ({
    project_id: 7, done: 0, total: 5, complete: false,
    steps: ['test', 'run', 'passed', 'schedule', 'team'].map((key) => ({ key, done: false })),
    ...over,
});

describe('stepViews', () => {
    it('marks only the first open step as the one to do now', () => {
        const views = stepViews(data({
            steps: [{ key: 'test', done: true }, { key: 'run', done: false }, { key: 'passed', done: false }],
        }));
        expect(views.map((v) => [v.key, v.done, v.current])).toEqual([
            ['test', true, false], ['run', false, true], ['passed', false, false],
        ]);
    });

    it('has no current step when everything is done', () => {
        const views = stepViews(data({ steps: [{ key: 'test', done: true }, { key: 'run', done: true }] }));
        expect(views.some((v) => v.current)).toBe(false);
    });

    it('sends people straight to the editor, the suite and the run when they exist', () => {
        const none = stepViews(data());
        expect(none.map((v) => v.to)).toEqual(['/suites', '/suites', '/runs', '/schedules', '/workspace']);
        expect(none[0].action).toBe('Go to suites');

        const some = stepViews(data({ first_suite_id: 12, latest_run_id: 340 }));
        expect(some.map((v) => v.to)).toEqual(['/suites/12/builder', '/suites/12', '/runs/340', '/schedules', '/workspace']);
        expect(some[0].action).toBe('Create a test');
    });

    it('skips a step it does not know, and keeps the order given', () => {
        const views = stepViews(data({ steps: [{ key: 'team', done: false }, { key: 'brand-new', done: false }, { key: 'test', done: true }] }));
        expect(views.map((v) => v.key)).toEqual(['team', 'test']);
        expect(views[0].current).toBe(true);
    });
});
