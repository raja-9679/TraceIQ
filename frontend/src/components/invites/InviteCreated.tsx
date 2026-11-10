import { Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

export type CreatedInvite = { email: string; link: string; emailed: boolean };

/**
 * What an inviter sees right after an invitation is created (or re-sent):
 * whether it was emailed, and the link, which is shown only now.
 *
 * `emailed` means the message was handed to the mail queue, not that it
 * arrived, so the link is offered either way.
 */
export function InviteCreated({ invite, onDone, id }: { invite: CreatedInvite; onDone: () => void; id: string }) {
    return (
        <>
            <DialogHeader>
                <DialogTitle>{invite.emailed ? 'Invitation sent' : 'Invitation created'}</DialogTitle>
                <DialogDescription>
                    {invite.emailed
                        ? <>An invitation email is on its way to {invite.email}. If it does not arrive, send them this link yourself; it is shown only now and works for 7 days.</>
                        : <>Nothing was emailed: email is not set up on this TraceIQ, or this is a demo workspace. Send this link to {invite.email} yourself; it is shown only now and works for 7 days.</>}
                </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-1.5">
                <Label htmlFor={id} className="text-sm font-semibold text-ink">Invitation link</Label>
                <Input id={id} className="h-11 rounded-xl border-border bg-white font-mono text-xs" readOnly value={invite.link}
                    onFocus={(e) => e.currentTarget.select()} />
            </div>
            <DialogFooter>
                <Button type="button" variant="outline" onClick={onDone}>Done</Button>
                <Button type="button" onClick={() => { navigator.clipboard.writeText(invite.link); toast.success('Invitation link copied'); }}>
                    <Copy className="h-4 w-4" /> Copy invitation link
                </Button>
            </DialogFooter>
        </>
    );
}
