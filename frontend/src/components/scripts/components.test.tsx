// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PageScript } from '@/api/estates';
import ScriptsTable from './ScriptsTable';
import ChangeHistory from './ChangeHistory';

afterEach(cleanup);

const base: PageScript = {
    id: 1, page_id: 9, script_key: 'https://js.stripe.com/v3/', kind: 'external', src: 'https://js.stripe.com/v3/',
    sha384: 'sha384-abcdefghijklmnopqrstuvwxyz', integrity: null, third_party: true, present: true,
    first_seen_at: '2026-10-01T00:00:00', last_seen_at: '2026-10-15T00:00:00', removed_at: null,
    authorized: false, justification: null, authorized_by_id: null, authorized_at: null,
};

describe('ScriptsTable', () => {
    it('will not approve without a justification', async () => {
        const onApprove = vi.fn();
        render(<ScriptsTable scripts={[base]} approvingId={null} onApprove={onApprove} onRevoke={() => undefined} />);
        await userEvent.click(screen.getByRole('button', { name: 'Approve…' }));
        const box = screen.getByLabelText(/Why is this script needed/);
        await userEvent.click(screen.getByRole('button', { name: 'Approve script' }));
        expect(onApprove).not.toHaveBeenCalled();
        expect(screen.getByRole('alert').textContent).toMatch(/required/);
        await userEvent.type(box, 'Card tokenisation (Stripe Elements)');
        await userEvent.click(screen.getByRole('button', { name: 'Approve script' }));
        expect(onApprove).toHaveBeenCalledWith(base, 'Card tokenisation (Stripe Elements)');
    });

    it('shows approval with its justification and a labelled withdraw control', async () => {
        const onRevoke = vi.fn();
        const approved = { ...base, authorized: true, justification: 'Payments SDK' };
        render(<ScriptsTable scripts={[approved]} approvingId={null} onApprove={() => undefined} onRevoke={onRevoke} />);
        expect(screen.getByText('Justification: Payments SDK')).toBeTruthy();
        await userEvent.click(screen.getByRole('button', { name: 'Withdraw approval' }));
        expect(onRevoke).toHaveBeenCalledWith(approved);
    });

    it('labels inline scripts by hash and flags missing SRI', () => {
        render(<ScriptsTable scripts={[{ ...base, kind: 'inline', src: null, third_party: false }]}
            approvingId={null} onApprove={() => undefined} onRevoke={() => undefined} />);
        expect(screen.getByText(/inline script sha384-/)).toBeTruthy();
        expect(screen.getByText('none')).toBeTruthy();
    });
});

describe('ChangeHistory', () => {
    it('lists changes newest first as given', () => {
        render(<ChangeHistory changes={[
            { id: 2, kind: 'script_added', summary: 'New script: https://skim.example.net/x.js', detail: null, created_at: '2026-10-15T06:00:00' },
            { id: 1, kind: 'baseline', summary: 'Inventory started with 3 script(s)', detail: null, created_at: '2026-10-08T06:00:00' },
        ]} />);
        const items = screen.getAllByRole('listitem');
        expect(items[0].textContent).toMatch(/script added.*skim/);
    });
});
