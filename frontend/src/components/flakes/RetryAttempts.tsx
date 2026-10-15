import { RotateCcw } from 'lucide-react';
import type { RetryAttempt } from './flakeHelpers';

// Every try of a retried test (result_payload.attempts, written by the
// aggregator from the worker's report). Workers that predate it send none,
// and a test that passed first time has none — then this renders nothing.

export function RetryAttempts({ attempts }: { attempts: RetryAttempt[] }) {
  if (attempts.length < 2) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4">
      <p className="text-[10px] font-bold text-slate-600 mb-2 uppercase tracking-wider flex items-center gap-1.5">
        <RotateCcw size={14} /> Attempts ({attempts.length})
      </p>
      <ol className="space-y-2">
        {attempts.map((a) => {
          const passed = a.status === 'passed';
          return (
            <li key={a.attempt} className="text-xs">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-700">Attempt {a.attempt}</span>
                <span className={passed ? 'text-emerald-700 font-semibold' : 'text-rose-700 font-semibold'}>
                  {a.status}
                </span>
                {typeof a.duration_ms === 'number' && (
                  <span className="text-slate-400 tabular-nums">{(a.duration_ms / 1000).toFixed(1)}s</span>
                )}
              </div>
              {a.error && (
                <pre className="mt-1 text-[11px] text-slate-600 whitespace-pre-wrap font-mono max-h-24 overflow-y-auto">
                  {a.error}
                </pre>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
