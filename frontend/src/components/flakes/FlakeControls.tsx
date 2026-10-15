import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  flakesApi, FlakinessEntry, QUARANTINE_DEFAULT_DAYS, QUARANTINE_MAX_DAYS,
} from '@/api/flakes';
import { errorDetail, useOwnerCandidates } from './flakeHelpers';

// Shared by the Flaky Tests page in both the original and redesigned layouts:
// the quarantine dialog (reason + length + owner), the owner picker and the
// quarantine status line. Data and rules live in the backend
// (app/api/flake_records.py, app/services/flake_policy.py).

const UNOWNED = 'none';

export function FlakeOwnerSelect({ entry, projectId }: { entry: FlakinessEntry; projectId: number }) {
  const queryClient = useQueryClient();
  const { data: people } = useOwnerCandidates(projectId);
  const mutation = useMutation({
    mutationFn: (ownerId: number | null) => flakesApi.setOwner(entry.flake_id!, ownerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['flakiness', projectId] }),
    onError: (err: unknown) => toast.error(`Could not change the owner: ${errorDetail(err)}`),
  });
  const current = entry.owner ? String(entry.owner.id) : UNOWNED;
  // The current owner may have lost editor access since; keep them visible.
  const options = [...(people || [])];
  if (entry.owner && !options.some((p) => p.id === entry.owner!.id)) options.unshift(entry.owner);

  return (
    <Select
      value={current}
      disabled={!entry.flake_id || mutation.isPending}
      onValueChange={(v) => mutation.mutate(v === UNOWNED ? null : Number(v))}
    >
      <SelectTrigger aria-label={`Owner of ${entry.name}`} className="h-8 w-[170px] rounded-lg text-xs">
        <SelectValue placeholder="Unowned" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={UNOWNED}>Unowned</SelectItem>
        {options.map((p) => (
          <SelectItem key={p.id} value={String(p.id)}>{p.full_name || p.email}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function QuarantineDialog({
  entry, projectId, open, onOpenChange,
}: {
  entry: FlakinessEntry;
  projectId: number;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const { data: people } = useOwnerCandidates(projectId);
  const [reason, setReason] = useState('');
  const [days, setDays] = useState(String(QUARANTINE_DEFAULT_DAYS));
  const [owner, setOwner] = useState(entry.owner ? String(entry.owner.id) : '');

  const daysNum = Number(days);
  const daysValid = Number.isInteger(daysNum) && daysNum >= 1 && daysNum <= QUARANTINE_MAX_DAYS;
  const valid = reason.trim().length > 0 && daysValid;

  const mutation = useMutation({
    mutationFn: () => flakesApi.quarantine(entry.flake_id!, {
      reason: reason.trim(),
      days: daysNum,
      owner_user_id: owner ? Number(owner) : null,
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['flakiness', projectId] });
      toast.success(`"${entry.name}" quarantined for ${daysNum} day${daysNum === 1 ? '' : 's'}`);
      onOpenChange(false);
      setReason('');
    },
    onError: (err: unknown) => toast.error(`Could not quarantine: ${errorDetail(err)}`),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Quarantine “{entry.name}”</DialogTitle>
          <DialogDescription>
            Quarantined cases are skipped when runs start. The quarantine ends on its own after the
            number of days you choose, and the case runs again.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => { e.preventDefault(); if (valid) mutation.mutate(); }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="quarantine-reason">Reason</Label>
            <Input
              id="quarantine-reason"
              value={reason}
              maxLength={500}
              placeholder="e.g. races the payment mock; fix tracked in JIRA-123"
              onChange={(e) => setReason(e.target.value)}
              autoFocus
            />
          </div>
          <div className="flex gap-3">
            <div className="space-y-1.5 w-28">
              <Label htmlFor="quarantine-days">Days</Label>
              <Input
                id="quarantine-days"
                type="number"
                min={1}
                max={QUARANTINE_MAX_DAYS}
                value={days}
                onChange={(e) => setDays(e.target.value)}
              />
            </div>
            <div className="space-y-1.5 flex-1">
              <Label>Owner</Label>
              <Select value={owner || 'me'} onValueChange={(v) => setOwner(v === 'me' ? '' : v)}>
                <SelectTrigger aria-label="Owner" className="h-10">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {!entry.owner && <SelectItem value="me">Me</SelectItem>}
                  {(people || []).map((p) => (
                    <SelectItem key={p.id} value={String(p.id)}>{p.full_name || p.email}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          {!daysValid && (
            <p className="text-xs text-rose-600">Choose between 1 and {QUARANTINE_MAX_DAYS} days.</p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!valid || mutation.isPending}>Quarantine</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
