import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Activity, RefreshCw, AlertCircle, ShieldAlert, ShieldCheck, Layers, Info, CheckCircle2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from '@/components/ui/table';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import { toast } from 'sonner';
import { getProjects } from '@/lib/api';
import { flakesApi, FlakinessEntry } from '@/api/flakes';

interface ApiError extends Error {
  response?: { data?: { detail?: string } };
}

const errorDetail = (err: unknown): string => {
  const e = err as ApiError;
  return e.response?.data?.detail || e.message || 'Unknown error';
};

// ---------------------------------------------------------------------------
// Flake score bar — 0.0 stable (emerald) → 1.0 pure flake (rose)
// ---------------------------------------------------------------------------

function FlakeScoreBar({ score }: { score: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  const tone = score >= 0.66 ? 'bg-rose-500' : score >= 0.33 ? 'bg-amber-500' : 'bg-emerald-500';
  const label = score >= 0.66 ? 'text-rose-700' : score >= 0.33 ? 'text-amber-700' : 'text-emerald-700';
  return (
    <div className="flex items-center gap-2 min-w-[140px]">
      <div className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
        <div className={`h-full rounded-full ${tone} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs font-bold tabular-nums ${label}`}>{pct}%</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Row — quarantine/release act on all FlakeRecords for the case
// ---------------------------------------------------------------------------

function FlakeRow({ entry, projectId }: { entry: FlakinessEntry; projectId: number }) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: async (action: 'quarantine' | 'release') => {
      // The flakiness summary is keyed by test case, but quarantine/release
      // operate on individual FlakeRecord rows (a case may have a case-level
      // row plus per-step rows). Fetch them and toggle each.
      const records = await flakesApi.list(entry.test_case_id);
      if (records.length === 0) {
        throw new Error('No flake records found for this test case');
      }
      await Promise.all(
        records.map((r) =>
          action === 'quarantine' ? flakesApi.quarantine(r.id) : flakesApi.release(r.id),
        ),
      );
      return action;
    },
    onSuccess: (action) => {
      queryClient.invalidateQueries({ queryKey: ['flakiness', projectId] });
      toast.success(action === 'quarantine'
        ? `"${entry.name}" quarantined — it will be skipped at dispatch`
        : `"${entry.name}" released back into runs`);
    },
    onError: (err: unknown) => toast.error(`Failed to update quarantine: ${errorDetail(err)}`),
  });

  return (
    <TableRow className="border-slate-100 hover:bg-slate-50/50 transition-colors align-top">
      <TableCell className="py-4">
        <div className="font-bold text-slate-800 text-sm">{entry.name}</div>
        <div className="text-xs text-slate-400 mt-0.5">case #{entry.test_case_id}</div>
      </TableCell>
      <TableCell className="py-4">
        <FlakeScoreBar score={entry.flake_score} />
      </TableCell>
      <TableCell className="py-4 text-sm text-slate-600 tabular-nums">{entry.sample_count}</TableCell>
      <TableCell className="py-4">
        {entry.recent_failures > 0 ? (
          <span className="text-sm font-semibold text-rose-600 tabular-nums">{entry.recent_failures}</span>
        ) : (
          <span className="text-sm text-slate-400 tabular-nums">0</span>
        )}
        {entry.last_failure_message && (
          <p className="text-[11px] text-slate-400 mt-0.5 line-clamp-2 max-w-[280px]" title={entry.last_failure_message}>
            {entry.last_failure_message}
          </p>
        )}
      </TableCell>
      <TableCell className="py-4">
        {entry.is_quarantined ? (
          <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-amber-50 text-amber-700 border-amber-200">
            <ShieldAlert className="w-3 h-3 mr-1" /> quarantined
          </Badge>
        ) : (
          <Badge variant="outline" className="rounded-md text-[10px] font-bold uppercase bg-emerald-50 text-emerald-700 border-emerald-200">
            <CheckCircle2 className="w-3 h-3 mr-1" /> active
          </Badge>
        )}
      </TableCell>
      <TableCell className="py-4 text-right">
        {entry.is_quarantined ? (
          <Button
            size="sm"
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate('release')}
            className="h-8 rounded-lg text-xs border-slate-200 text-emerald-700 hover:bg-emerald-50 hover:border-emerald-200"
          >
            <ShieldCheck className="w-3.5 h-3.5 mr-1" /> Release
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate('quarantine')}
            className="h-8 rounded-lg text-xs border-amber-200 text-amber-700 hover:bg-amber-50"
          >
            <ShieldAlert className="w-3.5 h-3.5 mr-1" /> Quarantine
          </Button>
        )}
      </TableCell>
    </TableRow>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function FlakyTests() {
  const [projectId, setProjectId] = useState<number | null>(() => {
    const saved = localStorage.getItem('activeProjectId');
    return saved ? parseInt(saved) : null;
  });

  useEffect(() => {
    const handleProjectChange = () => {
      const saved = localStorage.getItem('activeProjectId');
      setProjectId(saved ? parseInt(saved) : null);
    };
    window.addEventListener('projectChanged', handleProjectChange);
    return () => window.removeEventListener('projectChanged', handleProjectChange);
  }, []);

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => getProjects(),
  });

  const handleProjectSelect = (idStr: string) => {
    const id = parseInt(idStr);
    setProjectId(id);
    localStorage.setItem('activeProjectId', id.toString());
    window.dispatchEvent(new Event('projectChanged'));
  };

  const { data: entries, isLoading, error } = useQuery({
    queryKey: ['flakiness', projectId],
    queryFn: () => flakesApi.projectFlakiness(projectId!),
    enabled: !!projectId,
  });

  return (
    <div className="max-w-[1200px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Resilience</span>
        </div>
        <div className="flex flex-col sm:flex-row sm:items-end gap-4">
          <div>
            <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Flaky Tests</h1>
            <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
              Cases whose recent runs alternate pass/fail under identical conditions. Quarantine a flaky case
              to skip it at dispatch so it stops gating regressions, then release it once it's fixed.
            </p>
          </div>
          <div className="sm:ml-auto shrink-0">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-1.5">Project</p>
            <Select value={projectId?.toString() ?? ''} onValueChange={handleProjectSelect}>
              <SelectTrigger className="w-[240px] h-10 rounded-xl bg-white border-slate-200">
                <div className="flex items-center gap-2 min-w-0">
                  <Layers className="w-4 h-4 text-indigo-500 shrink-0" />
                  <SelectValue placeholder="Select a project" />
                </div>
              </SelectTrigger>
              <SelectContent>
                {(projects || []).map((p) => (
                  <SelectItem key={p.id} value={p.id.toString()}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {!projectId ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <AlertCircle className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No Project Selected</h3>
          <p className="text-slate-500 text-sm">Select a project above to review its flaky tests.</p>
        </div>
      ) : (
        <>
          <div className="flex items-start gap-2 text-xs text-slate-500 bg-indigo-50/60 border border-indigo-100 rounded-xl px-3 py-2.5 mb-4">
            <Info className="w-4 h-4 text-indigo-400 shrink-0 mt-px" />
            <p>
              The flake score ranges from <span className="font-semibold">0% (stable)</span> to{' '}
              <span className="font-semibold">100% (pure flake)</span> and is maintained by the result-aggregator
              pipeline. Quarantining toggles every flake record for the case.
            </p>
          </div>

          {isLoading ? (
            <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
          ) : error ? (
            <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
              <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
              <p className="text-rose-700 font-semibold">Failed to load flaky tests</p>
              <p className="text-rose-600/80 text-sm">{errorDetail(error)}</p>
            </div>
          ) : !entries || entries.length === 0 ? (
            <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
              <Activity className="w-10 h-10 text-slate-300 mx-auto mb-4" />
              <h3 className="text-lg font-bold text-slate-800 mb-1">No flaky tests detected</h3>
              <p className="text-slate-500 text-sm max-w-md mx-auto">
                When a case's retry stream shows alternating pass/fail under identical conditions, it appears
                here with a flake score so you can quarantine it.
              </p>
            </div>
          ) : (
            <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader className="bg-slate-50/50">
                    <TableRow className="border-slate-100 hover:bg-transparent">
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Test Case</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[200px]">Flake Score</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[90px]">Samples</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12">Recent failures</TableHead>
                      <TableHead className="font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[130px]">Status</TableHead>
                      <TableHead className="text-right font-bold text-slate-500 uppercase tracking-widest text-xs h-12 w-[150px]">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {entries.map((entry) => (
                      <FlakeRow key={entry.test_case_id} entry={entry} projectId={projectId} />
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
