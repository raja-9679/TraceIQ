import { describe, expect, it } from 'vitest';
import { draftErrors, draftFrom, draftToInput, groupRules, limitSummary, parseLimit, usageLabel } from './agentHelpers';
import type { PolicyRule } from '@/api/policies';

describe('agent control drafts', () => {
    it('round-trips inherit / no limit / cap', () => {
        const d = draftFrom('bot', { agent_id: 'bot', blocked: true, blocked_reason: 'loop',
            max_runs: 5, max_llm_tokens: 0, max_proposals: null, max_minutes: null });
        expect(d.limits).toEqual({ max_runs: '5', max_llm_tokens: '0', max_proposals: '', max_minutes: '' });
        expect(draftToInput(d)).toEqual({ agent_id: 'bot', blocked: true, blocked_reason: 'loop',
            max_runs: 5, max_llm_tokens: 0, max_proposals: null, max_minutes: null });
    });
    it('drops the reason when not blocked and validates numbers', () => {
        const d = { ...draftFrom('bot', null), blocked_reason: 'x' };
        expect(draftToInput(d).blocked_reason).toBeNull();
        expect(parseLimit(' 7 ')).toBe(7);
        expect(parseLimit('')).toBeNull();
        expect(parseLimit('-1')).toBeUndefined();
        expect(parseLimit('1.5')).toBeUndefined();
        expect(draftErrors({ ...d, agent_id: ' ', limits: { ...d.limits, max_runs: 'x' } })).toHaveLength(2);
    });
});

describe('labels', () => {
    it('summarises limits and usage', () => {
        expect(limitSummary({ max_runs: 3, max_llm_tokens: 20000, max_proposals: 0, max_minutes: 30 }))
            .toBe('3 runs · 20k tokens · 30 min');
        expect(limitSummary({ max_runs: 0, max_llm_tokens: 0, max_proposals: 0, max_minutes: 0 })).toBe('No limits');
        expect(usageLabel(2, 5)).toBe('2 / 5');
        expect(usageLabel(2, 0)).toBe('2');
    });
    it('groups rules by action in a stable order', () => {
        const r = (action: string, name: string) => ({ action, name } as PolicyRule);
        const groups = groupRules([r('security.scan', 'a'), r('ai.use', 'b'), r('run.dispatch', 'c'), r('ai.use', 'd')]);
        expect(groups.map((g) => g.action)).toEqual(['run.dispatch', 'ai.use', 'security.scan']);
        expect(groups[1].rules.map((x) => x.name)).toEqual(['b', 'd']);
    });
});

describe('inheritance', () => {
    it('workspace default wins over the instance, null inherits', async () => {
        const { inheritedLimits } = await import('./agentHelpers');
        const inst = { max_runs: 50, max_llm_tokens: 1000, max_proposals: 5, max_minutes: 60 };
        expect(inheritedLimits(null, inst)).toEqual(inst);
        expect(inheritedLimits({ agent_id: '*', blocked: false, blocked_reason: null, max_runs: 0,
            max_llm_tokens: null, max_proposals: 2, max_minutes: null }, inst))
            .toEqual({ max_runs: 0, max_llm_tokens: 1000, max_proposals: 2, max_minutes: 60 });
    });
});
