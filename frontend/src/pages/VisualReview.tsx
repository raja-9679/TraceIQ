import { useState, useEffect, useMemo } from 'react';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Images, RefreshCw, AlertCircle, Check, ShieldCheck, ChevronDown, ChevronRight,
  Monitor, Smartphone, Clock, ImageOff, ExternalLink, Trash2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { toast } from 'sonner';
import {
  visualBaselinesApi, findLatestComparison, resolveImageUrl,
  VisualBaseline, VisualComparisonArtifacts,
} from '@/api/visual';
import { getTestCaseInfo, TestCaseInfo } from '@/api/proposals';

const formatDate = (dateString?: string | null) => {
  if (!dateString) return '—';
  return new Date(dateString).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

// ---------------------------------------------------------------------------
// Comparison panel — baseline | actual | diff, resolved to presigned URLs
// ---------------------------------------------------------------------------

interface ComparisonData {
  artifacts: VisualComparisonArtifacts | null;
  baselineUrl: string | null;
  candidateUrl: string | null;
  diffUrl: string | null;
}

function ImagePane({ label, url, tone }: { label: string; url: string | null; tone: string }) {
  return (
    <div className="flex-1 min-w-[220px]">
      <p className={`text-[10px] font-bold uppercase tracking-widest mb-2 ${tone}`}>{label}</p>
      {url ? (
        <a href={url} target="_blank" rel="noreferrer" className="group block relative">
          <img
            src={url}
            alt={label}
            className="w-full rounded-xl border border-slate-200 shadow-sm bg-white object-contain max-h-[420px]"
          />
          <span className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity bg-white/90 rounded-md p-1 shadow">
            <ExternalLink className="w-3.5 h-3.5 text-slate-500" />
          </span>
        </a>
      ) : (
        <div className="w-full h-40 rounded-xl border border-dashed border-slate-200 bg-slate-50 flex flex-col items-center justify-center text-slate-400">
          <ImageOff className="w-6 h-6 mb-1.5" />
          <span className="text-xs">Not available</span>
        </div>
      )}
    </div>
  );
}

function ComparisonPanel({ baseline, caseName }: { baseline: VisualBaseline; caseName: string }) {
  const queryClient = useQueryClient();
  const [kept, setKept] = useState(false);

  const { data, isLoading, error } = useQuery<ComparisonData>({
    queryKey: ['visual-comparison', baseline.id],
    queryFn: async () => {
      const [artifacts, baselineUrl] = await Promise.all([
        findLatestComparison(baseline.test_case_id, baseline.step_id).catch(() => null),
        resolveImageUrl(baseline.image_url).catch(() => null),
      ]);
      const [candidateUrl, diffUrl] = await Promise.all([
        artifacts?.candidateKey ? resolveImageUrl(artifacts.candidateKey).catch(() => null) : Promise.resolve(null),
        artifacts?.diffKey ? resolveImageUrl(artifacts.diffKey).catch(() => null) : Promise.resolve(null),
      ]);
      return { artifacts, baselineUrl, candidateUrl, diffUrl };
    },
    staleTime: 30_000,
  });

  const acceptMutation = useMutation({
    mutationFn: async () => {
      if (!data?.artifacts?.runId) throw new Error('No candidate screenshot available');
      // Durable promotion: the backend copies the run's candidate screenshot
      // into a stable baseline object (survives artifact cleanup / URL expiry)
      // and upserts the baseline row for this case/step/browser/device.
      await visualBaselinesApi.promote({
        test_case_id: baseline.test_case_id,
        step_id: baseline.step_id,
        run_id: data.artifacts.runId,
        browser: baseline.browser,
        device: baseline.device,
        tolerance: baseline.tolerance,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
      toast.success('New baseline accepted', {
        description: `The latest screenshot is now the pinned baseline for "${caseName}".`,
      });
    },
    onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
      toast.error(`Failed to accept baseline: ${err.response?.data?.detail || err.message}`),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400 py-6 justify-center">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading comparison images…
      </div>
    );
  }
  if (error || !data) {
    return <p className="text-sm text-rose-600 py-3">Failed to load comparison for this baseline.</p>;
  }

  const { artifacts } = data;

  return (
    <div className="space-y-4">
      {artifacts ? (
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Clock className="w-3.5 h-3.5" />
          Latest capture from run #{artifacts.runId} ({formatDate(artifacts.runCreatedAt)})
          <Badge
            variant="outline"
            className={`rounded-md text-[10px] font-bold uppercase ${artifacts.runStatus === 'passed'
              ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
              : 'bg-rose-50 text-rose-700 border-rose-200'}`}
          >
            {artifacts.runStatus}
          </Badge>
        </div>
      ) : (
        <p className="text-xs text-slate-400 italic">
          No recent run captured a screenshot for this visual step — showing the pinned baseline only.
        </p>
      )}

      <div className="flex flex-wrap gap-4">
        <ImagePane label="Baseline" url={data.baselineUrl} tone="text-slate-400" />
        <ImagePane label="Actual (latest run)" url={data.candidateUrl} tone="text-sky-500" />
        <ImagePane label="Pixel Diff" url={data.diffUrl} tone="text-rose-400" />
      </div>

      <div className="flex flex-wrap items-center gap-2 pt-1">
        <Button
          size="sm"
          disabled={!data.artifacts?.candidateKey || acceptMutation.isPending}
          onClick={() => acceptMutation.mutate()}
          className="bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl h-9 px-4"
          title={data.artifacts?.candidateKey ? 'Pin the latest screenshot as the new baseline' : 'No candidate screenshot available from recent runs'}
        >
          {acceptMutation.isPending ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Check className="w-4 h-4 mr-1.5" />}
          Accept new baseline
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={kept || acceptMutation.isPending}
          onClick={() => {
            setKept(true);
            toast.info('Baseline kept', { description: 'The existing baseline remains pinned; no change was made.' });
          }}
          className="text-slate-600 hover:bg-slate-50 border-slate-200 rounded-xl h-9 px-4"
        >
          <ShieldCheck className="w-4 h-4 mr-1.5" /> {kept ? 'Baseline kept' : 'Keep baseline'}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Baseline row
// ---------------------------------------------------------------------------

function BaselineRow({ baseline, caseInfo }: { baseline: VisualBaseline; caseInfo?: TestCaseInfo }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const caseName = caseInfo?.name || `Case #${baseline.test_case_id}`;

  const deleteMutation = useMutation({
    mutationFn: () => visualBaselinesApi.delete(baseline.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['visual-baselines'] });
      toast.success('Baseline deleted', {
        description: `The pinned baseline for "${caseName}" (step ${baseline.step_id}) was removed.`,
      });
    },
    onError: (err: Error & { response?: { data?: { detail?: string } } }) =>
      toast.error(`Failed to delete baseline: ${err.response?.data?.detail || err.message}`),
  });

  return (
    <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
      <div className="w-full flex items-center gap-3 px-5 py-4 hover:bg-slate-50/60 transition-colors">
        <button
          className="flex items-center gap-3 text-left flex-1 min-w-0"
          onClick={() => setExpanded((e) => !e)}
        >
          {expanded ? <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" /> : <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />}
          <div className="min-w-0">
            <span className="font-bold text-slate-800 truncate block">{caseName}</span>
            <span className="text-xs text-slate-400 font-mono">step {baseline.step_id}</span>
          </div>
        </button>
        <div className="flex items-center gap-2 shrink-0">
          <Badge variant="outline" className="rounded-lg bg-slate-50 text-slate-600 border-slate-200 text-[10px] font-semibold">
            <Monitor className="w-3 h-3 mr-1" /> {baseline.browser}
          </Badge>
          {baseline.device && (
            <Badge variant="outline" className="rounded-lg bg-slate-50 text-slate-600 border-slate-200 text-[10px] font-semibold">
              <Smartphone className="w-3 h-3 mr-1" /> {baseline.device}
            </Badge>
          )}
          <Badge variant="outline" className="rounded-lg bg-indigo-50 text-indigo-700 border-indigo-200 text-[10px] font-bold">
            tolerance {(baseline.tolerance * 100).toFixed(1)}%
          </Badge>
          <span className="text-xs text-slate-400 whitespace-nowrap hidden sm:inline">{formatDate(baseline.created_at)}</span>
          <Button
            size="sm"
            variant="outline"
            disabled={deleteMutation.isPending}
            onClick={() => {
              if (window.confirm(`Delete the baseline for "${caseName}" (step ${baseline.step_id})? Subsequent runs will have nothing to compare against.`)) {
                deleteMutation.mutate();
              }
            }}
            className="h-8 rounded-lg text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50 border-rose-200"
            title="Delete baseline"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
      {expanded && (
        <div className="border-t border-slate-100 px-5 py-4 bg-slate-50/30">
          <ComparisonPanel baseline={baseline} caseName={caseName} />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function VisualReview() {
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

  const { data: baselines, isLoading, error } = useQuery({
    queryKey: ['visual-baselines', projectId],
    queryFn: () => visualBaselinesApi.list(projectId ? { projectId } : undefined),
  });

  // Resolve case info for each unique baseline case so we can show names and
  // filter to the active project (baselines carry no project_id themselves).
  const caseIds = useMemo(
    () => Array.from(new Set((baselines || []).map((b) => b.test_case_id))),
    [baselines],
  );
  const caseQueries = useQueries({
    queries: caseIds.map((id) => ({
      queryKey: ['case', id],
      queryFn: () => getTestCaseInfo(id),
      staleTime: 60_000,
      retry: false,
    })),
  });
  const casesById = useMemo(() => {
    const map = new Map<number, TestCaseInfo>();
    caseQueries.forEach((q, i) => {
      if (q.data) map.set(caseIds[i], q.data);
    });
    return map;
  }, [caseQueries, caseIds]);
  const casesResolving = caseQueries.some((q) => q.isLoading);

  const visibleBaselines = useMemo(() => {
    if (!baselines) return [];
    if (!projectId) return baselines;
    return baselines.filter((b) => {
      const info = casesById.get(b.test_case_id);
      // Keep rows whose case is still resolving (or inaccessible) out of view
      // only when we positively know they belong to another project.
      if (!info) return casesResolving;
      return info.project_id === projectId;
    });
  }, [baselines, projectId, casesById, casesResolving]);

  return (
    <div className="max-w-[1400px] mx-auto pb-16">
      <div className="pt-2 pb-6 mb-6 border-b border-slate-200/60">
        <div className="flex items-center text-sm text-slate-400 gap-2 mb-3">
          <span className="font-bold text-slate-700 bg-white px-2.5 py-1 rounded-md border border-slate-200 shadow-sm">Visual Regression</span>
        </div>
        <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Visual Review</h1>
        <p className="text-slate-500 max-w-2xl text-base leading-relaxed mt-2">
          Compare pinned screenshot baselines against the latest run captures. Accept intentional UI changes as new baselines, or keep the current baseline when the drift is a regression.
        </p>
      </div>

      {isLoading || (baselines && baselines.length > 0 && casesResolving && visibleBaselines.length === 0) ? (
        <div className="p-12 flex justify-center"><RefreshCw className="animate-spin text-muted-foreground w-6 h-6" /></div>
      ) : error ? (
        <div className="p-10 text-center bg-rose-50 border border-rose-200 rounded-2xl">
          <AlertCircle className="w-8 h-8 text-rose-500 mx-auto mb-3" />
          <p className="text-rose-700 font-semibold">Failed to load visual baselines</p>
          <p className="text-rose-600/80 text-sm">{(error as Error).message}</p>
        </div>
      ) : !baselines || baselines.length === 0 || visibleBaselines.length === 0 ? (
        <div className="p-14 text-center bg-white border border-slate-200 rounded-2xl">
          <Images className="w-10 h-10 text-slate-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-slate-800 mb-1">No visual baselines{projectId ? ' in this project' : ''}</h3>
          <p className="text-slate-500 text-sm max-w-md mx-auto">
            Add an <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs">expect-visual-match</code> step to a test case and pin a baseline screenshot — comparisons from subsequent runs will appear here for review.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {visibleBaselines.map((b) => (
            <BaselineRow key={b.id} baseline={b} caseInfo={casesById.get(b.test_case_id)} />
          ))}
        </div>
      )}
    </div>
  );
}
