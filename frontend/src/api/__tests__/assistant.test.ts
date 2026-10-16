import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn();
const post = vi.fn();
const del = vi.fn();
vi.mock('@/lib/api', () => ({
    api: {
        get: (...a: unknown[]) => get(...a),
        post: (...a: unknown[]) => post(...a),
        delete: (...a: unknown[]) => del(...a),
    },
}));

import { assistantApi } from '../assistant';

beforeEach(() => {
    get.mockReset().mockResolvedValue({ data: { ok: true } });
    post.mockReset().mockResolvedValue({ data: { ok: true } });
    del.mockReset().mockResolvedValue({ data: { status: 'deleted' } });
});

describe('assistantApi', () => {
    it('status and list are per workspace', async () => {
        await assistantApi.status(4);
        expect(get).toHaveBeenLastCalledWith('/assistant/status', { params: { workspace_id: 4 } });
        await assistantApi.list(4);
        expect(get).toHaveBeenLastCalledWith('/assistant/conversations', { params: { workspace_id: 4 } });
    });
    it('create sends the focus project only when set', async () => {
        await assistantApi.create(4, null);
        expect(post).toHaveBeenLastCalledWith('/assistant/conversations', { workspace_id: 4 });
        await assistantApi.create(4, 9);
        expect(post).toHaveBeenLastCalledWith('/assistant/conversations', { workspace_id: 4, project_id: 9 });
    });
    it('send posts the question with a long timeout', async () => {
        await assistantApi.send(12, 4, 'why?', 9);
        expect(post).toHaveBeenLastCalledWith('/assistant/conversations/12/messages',
            { text: 'why?', workspace_id: 4, project_id: 9 }, { timeout: 180000 });
    });
    it('get and remove address one conversation', async () => {
        await assistantApi.get(12);
        expect(get).toHaveBeenLastCalledWith('/assistant/conversations/12');
        await assistantApi.remove(12);
        expect(del).toHaveBeenLastCalledWith('/assistant/conversations/12');
    });
});
