import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
const put = vi.fn();
const del = vi.fn();
vi.mock('@/lib/api', () => ({
    api: {
        get: (...a: unknown[]) => get(...a),
        put: (...a: unknown[]) => put(...a),
        delete: (...a: unknown[]) => del(...a),
    },
}));

import { reliabilityApi } from '../reliability';

beforeEach(() => {
    get.mockReset().mockResolvedValue({ data: { ok: true } });
    put.mockReset().mockResolvedValue({ data: { ok: true } });
    del.mockReset().mockResolvedValue({});
});

describe('reliabilityApi', () => {
    it('DORA passes the window and only a set environment', async () => {
        await reliabilityApi.dora(3, 30);
        expect(get).toHaveBeenLastCalledWith('/projects/3/dora', { params: { days: 30 } });
        await reliabilityApi.dora(3, 90, 7);
        expect(get).toHaveBeenLastCalledWith('/projects/3/dora', { params: { days: 90, environment_id: 7 } });
    });
    it('deployments list and delete', async () => {
        await reliabilityApi.deployments(3);
        expect(get).toHaveBeenLastCalledWith('/projects/3/deployments', { params: { limit: 50 } });
        await reliabilityApi.deleteDeployment(9);
        expect(del).toHaveBeenCalledWith('/deployments/9');
    });
    it('SLO reads and writes', async () => {
        await reliabilityApi.projectSlo(3);
        expect(get).toHaveBeenLastCalledWith('/projects/3/slo', { params: {} });
        await reliabilityApi.monitorSlo(5, 7);
        expect(get).toHaveBeenLastCalledWith('/schedules/5/slo', { params: { window_days: 7 } });
        const body = { availability_target: 99.5, latency_p95_ms: null, window_days: 30 };
        await reliabilityApi.setMonitorSlo(5, body);
        expect(put).toHaveBeenCalledWith('/schedules/5/slo', body);
    });
    it('flake and dependency reports', async () => {
        await reliabilityApi.flakeTrend(3, 90);
        expect(get).toHaveBeenLastCalledWith('/analytics/projects/3/flake-trend', { params: { days: 90 } });
        await reliabilityApi.flakePatterns(3, 30);
        expect(get).toHaveBeenLastCalledWith('/analytics/projects/3/flake-patterns', { params: { days: 30 } });
        const out = await reliabilityApi.dependencyHealth(3, 30);
        expect(get).toHaveBeenLastCalledWith('/projects/3/dependency-health', { params: { days: 30 } });
        expect(out).toEqual({ ok: true });
    });
});
