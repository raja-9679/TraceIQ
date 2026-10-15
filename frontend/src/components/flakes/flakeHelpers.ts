import { useQuery } from '@tanstack/react-query';
import { flakesApi, FlakinessEntry } from '@/api/flakes';

// Non-component helpers for components/flakes/* (kept apart so those files
// export only components, which React fast refresh needs).

interface ApiError extends Error {
  response?: { data?: { detail?: string | { msg: string }[] } };
}

export const errorDetail = (err: unknown): string => {
  const detail = (err as ApiError).response?.data?.detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ');
  return detail || (err as Error).message || 'Unknown error';
};

export function useOwnerCandidates(projectId: number) {
  return useQuery({
    queryKey: ['flake-owner-candidates', projectId],
    queryFn: () => flakesApi.ownerCandidates(projectId),
    staleTime: 60_000,
  });
}

export function formatDate(iso: string): string {
  // Backend datetimes are naive UTC.
  const d = new Date(iso.endsWith('Z') ? iso : `${iso}Z`);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

/** "Quarantined until 29 Oct · auto: flake score 0.52 over 10 runs" */
export function quarantineSummary(entry: FlakinessEntry): string | null {
  if (entry.quarantine_state === 'none') return null;
  const parts: string[] = [];
  if (entry.quarantine_expires_at) {
    parts.push(entry.quarantine_state === 'expired'
      ? `Expired ${formatDate(entry.quarantine_expires_at)} — running again`
      : `Until ${formatDate(entry.quarantine_expires_at)}`);
  }
  if (entry.quarantine_reason) {
    parts.push(`${entry.quarantine_source === 'manual' ? '' : 'auto: '}${entry.quarantine_reason}`);
  }
  return parts.join(' · ') || null;
}

export interface RetryAttempt {
  attempt: number;
  status: string;
  error?: string;
  duration_ms?: number;
}

/** Every try of a retried test (result_payload.attempts). */
export function attemptsOf(result: { result_payload?: unknown }): RetryAttempt[] {
  const attempts = (result.result_payload as { attempts?: unknown } | null | undefined)?.attempts;
  return Array.isArray(attempts) ? (attempts as RetryAttempt[]) : [];
}
