import ServerMetricsPanel from '@/components/load/ServerMetricsPanel';
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { useParams, Link, useNavigate } from "react-router-dom";
import { api, getRun, getArtifactUrl, forceCompleteRun, createComparisonRun, apiWebSocketUrl, triggerRun } from "@/lib/api";
import { llmProvidersApi } from "@/api/llmProviders";
import { Brain, FileText, Video, ChevronDown, ChevronRight, CheckCircle, XCircle, Copy, Check, AlertTriangle, Activity, LayoutGrid, Bug, PlayCircle, Server, Globe, Zap, ExternalLink, GitCompareArrows, MonitorSmartphone } from "lucide-react";
import { useState, useEffect } from "react";
import { TraceTimeline } from "@/components/TraceTimeline";
import CreateTicketDialog from "@/components/CreateTicketDialog";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { KindChip, ListCard, ListRow, PageHeader, Pill, SplitView } from "@/components/redesign/primitives";
import * as Tabs from "@radix-ui/react-tabs";
import { motion, AnimatePresence } from "framer-motion";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { RetryAttempts } from '@/components/flakes/RetryAttempts';
import { attemptsOf } from '@/components/flakes/flakeHelpers';
import { InvestigationReport } from '@/components/evidence/InvestigationReport';
import { StepEvidenceTimeline } from '@/components/evidence/StepEvidenceTimeline';
import { HookOutcomes, RunDataSummary } from '@/components/suite-hooks/HookOutcomes';
import { hookOutcomesOf, resultsWithHookWarnings } from '@/components/suite-hooks/suiteHooks';
import { ReproBundleButton } from '@/components/ReproBundleButton';
import { ShareRunButton } from '@/components/reports/ShareLinks';
import SweepView from '@/components/frontend-quality/SweepView';
import {
    isInvestigation, stepAnchorId, stepEvidenceOf, stepIndexOfRef, type EvidenceFocus,
} from '@/components/evidence/evidenceHelpers';

function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export default function TestRunDetails() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || "0");
    const isValidRunId = !isNaN(runId) && runId > 0;
    const queryClient = useQueryClient();

    const navigate = useNavigate();
    const [testSearchTerm, setTestSearchTerm] = useState('');
    const [showForceCompleteDialog, setShowForceCompleteDialog] = useState(false);
    const [showCompareDialog, setShowCompareDialog] = useState(false);
    const [compareTargetUrl, setCompareTargetUrl] = useState('');

    const { data: run, isLoading } = useQuery({
        queryKey: ["run", runId],
        queryFn: () => getRun(runId),
        enabled: isValidRunId,
    });

    // WebSocket for Real-time Updates
    useEffect(() => {
        if (!isValidRunId) return;
        // If run is already finished, no need to connect (unless you want to watch for potential post-run updates, but unlikely)
        if (run?.status === 'passed' || run?.status === 'failed' || run?.status === 'error') return;

        // Browsers can't set headers on a WebSocket, so the JWT rides in the
        // subprotocol list (Sec-WebSocket-Protocol), not the URL — a URL ends
        // up in every access log. The server authorizes against the run's
        // project before accepting and echoes "traceiq.bearer" back.
        const token = localStorage.getItem("token");
        const ws = new WebSocket(
            apiWebSocketUrl(`/ws/runs/${runId}`),
            token ? ['traceiq.bearer', token] : undefined,
        );

        ws.onopen = () => {
            console.log("WebSocket Connected");
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                console.log("WS Update:", data);
                // Invalidate query to trigger refetch of full run data
                queryClient.invalidateQueries({ queryKey: ["run", runId] });
            } catch (e) {
                console.error("Error parsing WS message:", e);
            }
        };

        ws.onerror = (e) => console.error("WebSocket Error:", e);

        return () => {
            ws.close();
        };
    }, [runId, isValidRunId, run?.status, queryClient]);

    const { data: traceUrl } = useQuery({
        queryKey: ["trace", run?.trace_url],
        queryFn: () => getArtifactUrl(run!.trace_url!),
        enabled: !!run?.trace_url,
    });

    const { data: videoUrl } = useQuery({
        queryKey: ["video", run?.video_url],
        queryFn: () => getArtifactUrl(run!.video_url!),
        enabled: !!run?.video_url,
    });

    const { data: harUrl } = useQuery({
        queryKey: ["har", run?.har_url],
        queryFn: () => getArtifactUrl(run!.har_url!),
        enabled: !!run?.har_url,
    });

    // Force complete mutation
    const forceCompleteMutation = useMutation({
        mutationFn: () => forceCompleteRun(runId, "error", "Manually completed by administrator"),
        onSuccess: () => {
            toast.success("Test run marked as complete");
            queryClient.invalidateQueries({ queryKey: ["run", runId] });
            setShowForceCompleteDialog(false);
        },
        onError: (error: any) => {
            toast.error("Failed to complete test run", {
                description: error.response?.data?.detail || "An error occurred"
            });
        }
    });

    // On-demand AI failure analysis with a user-chosen provider ('' = default)
    const [analysisProviderId, setAnalysisProviderId] = useState<string>('');
    // Set by an evidence reference in the analysis panel: the result to
    // select and the step to scroll to / highlight.
    const [evidenceFocus, setEvidenceFocus] = useState<EvidenceFocus | null>(null);
    const { data: aiProviders } = useQuery({
        queryKey: ['llm-providers-active'],
        queryFn: llmProvidersApi.listActive,
        staleTime: 60_000,
        retry: false,
    });

    const analyzeMutation = useMutation({
        mutationFn: () => api.post(`/runs/${runId}/analyze`, {
            provider_id: analysisProviderId ? parseInt(analysisProviderId, 10) : null,
        }),
        onSuccess: () => {
            toast.success("Analysis queued — results appear here shortly");
            // Finished runs have no live WebSocket, so poll a few times.
            [4000, 9000, 16000, 25000].forEach((ms) =>
                setTimeout(() => queryClient.invalidateQueries({ queryKey: ["run", runId] }), ms));
        },
        onError: (error: any) => {
            toast.error("Failed to queue analysis", {
                description: error.response?.data?.detail || "An error occurred"
            });
        }
    });

    // Deployment comparison: re-run this run's suite against another URL and
    // land on the side-by-side diff view.
    const compareMutation = useMutation({
        mutationFn: () => createComparisonRun(runId, compareTargetUrl.trim()),
        onSuccess: (candidate) => {
            setShowCompareDialog(false);
            navigate(`/runs/${candidate.id}/comparison`);
        },
        onError: (error: any) => {
            toast.error("Failed to start comparison run", {
                description: error.response?.data?.detail || "An error occurred"
            });
        }
    });
    // Replay: same suite/case/browser/device, same {{fake.*}} seed.
    const replayMutation = useMutation({
        mutationFn: () => triggerRun(run!.test_suite_id, run!.test_case_id ?? undefined,
            run!.browser, run!.device ?? undefined, { data_seed: run!.data_seed ?? undefined }),
        onSuccess: (created) => {
            const first = Array.isArray(created) ? created[0] : created;
            if (first) navigate(`/runs/${first.id}`);
        },
        onError: (error: unknown) => {
            const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error("Failed to start the run", { description: detail || "An error occurred" });
        }
    });
    const isFinishedRun = run?.status === 'passed' || run?.status === 'failed' || run?.status === 'error';

    // Check if test is stuck (running for more than 10 minutes)
    const isStuckTest = run?.status === "running" && run?.created_at &&
        (new Date().getTime() - new Date(run.created_at).getTime()) > 10 * 60 * 1000;

    const hasIndividualVideos = run?.results?.some(r => !!r.video_url) ?? false;
    const hasIndividualTraces = run?.results?.some(r => !!r.trace_url) ?? false;

    if (!isValidRunId) return <div className="p-4">Invalid Run ID</div>;
    if (isLoading) return <div className="p-4">Loading...</div>;
    if (!run) return <div className="p-4">Run not found</div>;

    const passedCount = run.results?.filter(r => r.status === 'passed').length || 0;
    const failedCount = run.results?.filter(r => r.status === 'failed').length || 0;
    const totalCount = run.results?.length || 0;
    const passRate = totalCount > 0 ? Math.round((passedCount / totalCount) * 100) : 0;

    return (
        <Tabs.Root defaultValue="overview" className="flex flex-col gap-5 pb-12">
            <PageHeader
                crumbs={<>
                    <Link to="/runs" className="hover:text-ink">Runs</Link>
                    {run.suite_name && (<><span>/</span><Link to={`/suites/${run.test_suite_id}`} className="hover:text-ink">{run.suite_name}</Link></>)}
                    <span>/</span><span className="font-mono">#{run.id}</span>
                </>}
                title={run.test_case_name || run.suite_name || `Run #${run.id}`}
                badge={
                    <Pill tone={run.status === 'passed' ? 'mint' : (run.status === 'failed' || run.status === 'error') ? 'peach' : 'lavender'}>
                        {run.status === 'running' && <Activity size={12} className="animate-spin" />}
                        {run.status === 'passed' ? 'Passed' : run.status === 'failed' ? `Failed · ${failedCount} of ${totalCount}` : run.status === 'error' ? 'Error' : run.status === 'running' ? 'Running' : run.status}
                    </Pill>
                }
                subtitle={[
                    run.duration_ms ? `${(run.duration_ms / 1000).toFixed(1)}s` : null,
                    run.created_at ? new Date(run.created_at).toLocaleString() : null,
                    (run as any).git_commit ? String((run as any).git_commit).slice(0, 8) : null,
                    (run as any).git_branch || null,
                    (run as any).triggered_by ? `by ${(run as any).triggered_by}` : null,
                ].filter(Boolean).join(' · ')}
                actions={<>
                    {run.executor === 'manual' && (
                        <Button asChild><Link to={`/manual-runs/${run.id}`}>Open manual run</Link></Button>
                    )}
                    {run.baseline_run_id && (
                        <Button asChild variant="outline"><Link to={`/runs/${run.id}/comparison`}><GitCompareArrows size={16} /> View comparison</Link></Button>
                    )}
                    {isFinishedRun && !run.baseline_run_id && (
                        <Button variant="outline" onClick={() => setShowCompareDialog(true)} title="Re-run this suite against another deployment and diff the results">
                            <GitCompareArrows size={16} /> Compare deployment
                        </Button>
                    )}
                    <CreateTicketDialog runId={run.id} />
                    {isFinishedRun && run.project_id && <ShareRunButton runId={run.id} projectId={run.project_id} />}
                    {isStuckTest && (
                        <Button variant="destructive" onClick={() => setShowForceCompleteDialog(true)}>
                            <AlertTriangle size={16} /> Force complete
                        </Button>
                    )}
                </>}
                toolbar={
                            <Tabs.List aria-label="Run sections" className="-my-2 flex shrink-0 gap-1 self-stretch">
                                <Tabs.Trigger
                                    value="overview"
                                    className="flex min-h-12 items-center gap-2 px-3 text-sm font-medium text-ink-soft outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/20 data-[state=active]:font-semibold data-[state=active]:text-ink data-[state=active]:shadow-[inset_0_-2px_0_#1C1B22]"
                                >
                                    <LayoutGrid size={16} /> Overview
                                </Tabs.Trigger>
                                <Tabs.Trigger
                                    value="media"
                                    className="flex min-h-12 items-center gap-2 px-3 text-sm font-medium text-ink-soft outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/20 data-[state=active]:font-semibold data-[state=active]:text-ink data-[state=active]:shadow-[inset_0_-2px_0_#1C1B22]"
                                >
                                    <PlayCircle size={16} /> Traces
                                </Tabs.Trigger>
                                <Tabs.Trigger
                                    value="network"
                                    className="flex min-h-12 items-center gap-2 px-3 text-sm font-medium text-ink-soft outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/20 data-[state=active]:font-semibold data-[state=active]:text-ink data-[state=active]:shadow-[inset_0_-2px_0_#1C1B22]"
                                >
                                    <Server size={16} /> Network
                                </Tabs.Trigger>
                                {run.sweep_group && (
                                    <Tabs.Trigger
                                        value="sweep"
                                        className="flex min-h-12 items-center gap-2 px-3 text-sm font-medium text-ink-soft outline-none transition-colors hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/20 data-[state=active]:font-semibold data-[state=active]:text-ink data-[state=active]:shadow-[inset_0_-2px_0_#1C1B22]"
                                    >
                                        <MonitorSmartphone size={16} /> Sweep
                                    </Tabs.Trigger>
                                )}
                            </Tabs.List>
                }
            />

            <div className="flex flex-col gap-6">
                <RunDataSummary
                    seed={run.data_seed}
                    warningResults={resultsWithHookWarnings(run.results)}
                    onReplay={() => replayMutation.mutate()}
                    replaying={replayMutation.isPending}
                />

                <div className="grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-3">
                    <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
                        <span className="text-[13px] text-ink-soft">Duration</span>
                        <span className="text-[26px] font-bold leading-none tracking-tight tabular-nums">{run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : '—'}</span>
                    </div>
                    <div className="flex flex-col gap-1 rounded-2xl border border-border bg-white px-5 py-4">
                        <span className="text-[13px] text-ink-soft">Tests</span>
                        <span className="text-[26px] font-bold leading-none tracking-tight tabular-nums">{totalCount}</span>
                    </div>
                    <div className="col-span-full flex flex-col gap-2 rounded-2xl border border-border bg-white px-5 py-4 sm:col-span-2">
                        <span className="flex justify-between text-[13px] text-ink-soft"><span>Pass rate</span><span className="font-semibold text-ink">{passRate}%</span></span>
                        <div className="flex h-2 w-full overflow-hidden rounded-full bg-secondary" role="img" aria-label={`${passedCount} passed, ${failedCount} failed`}>
                            {totalCount > 0 && (<>
                                <div className="h-full bg-[#8FD3B6]" style={{ width: `${(passedCount / totalCount) * 100}%` }} />
                                <div className="h-full bg-[#E0603A]" style={{ width: `${(failedCount / totalCount) * 100}%` }} />
                            </>)}
                        </div>
                        <span className="flex justify-between text-xs text-ink-soft"><span>{passedCount} passed</span><span>{failedCount} failed</span></span>
                    </div>
                </div>

                {/* Main Content Tabs */}
                <div className="flex flex-col">

                    {run.sweep_group && (
                        <Tabs.Content value="sweep" className="focus:outline-none space-y-6">
                            <SweepView runId={run.id} />
                        </Tabs.Content>
                    )}

                    <Tabs.Content value="overview" className="focus:outline-none space-y-6">
                        {run.error_message && (
                            <div className="rounded-3xl border border-border bg-white p-6 shadow-[inset_4px_0_0_#E0603A]">
                                <h3 className="mb-3 flex items-center gap-2 font-semibold text-ink">
                                    <Bug size={18} /> Run error
                                </h3>
                                <pre className="whitespace-pre-wrap rounded-2xl bg-background p-4 font-mono text-[13px] text-ink">
                                    {run.error_message}
                                </pre>
                            </div>
                        )}

                        {(run.ai_analysis || run.status === 'failed' || run.status === 'error') && (
                            <div className="bg-tiq-callout rounded-3xl border border-white/90 p-6">
                                <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
                                    <h3 className="text-indigo-800 font-semibold flex items-center gap-2">
                                        <Brain size={18} /> TraceIQ thinks <span className="text-xs font-normal text-ink-soft">AI analysis · a guess, ranked by confidence</span>
                                    </h3>
                                    {(run.status === 'failed' || run.status === 'error') && (
                                        <div className="flex items-center gap-2">
                                            {(aiProviders || []).length > 0 && (
                                                <select
                                                    value={analysisProviderId}
                                                    onChange={(e) => setAnalysisProviderId(e.target.value)}
                                                    className="min-h-9 rounded-full border border-white bg-white/80 px-3 text-xs text-ink"
                                                    title="AI provider for this analysis"
                                                >
                                                    <option value="">Default provider</option>
                                                    {(aiProviders || []).map((p) => (
                                                        <option key={p.id} value={p.id}>
                                                            {p.name} ({p.model})
                                                        </option>
                                                    ))}
                                                </select>
                                            )}
                                            <button
                                                onClick={() => analyzeMutation.mutate()}
                                                disabled={analyzeMutation.isPending}
                                                className="min-h-9 rounded-full bg-ink px-4 text-xs font-semibold text-white transition-colors hover:bg-ink/85 disabled:opacity-50"
                                            >
                                                {analyzeMutation.isPending
                                                    ? 'Queuing…'
                                                    : run.ai_analysis ? 'Re-analyze' : 'Analyze failures'}
                                            </button>
                                        </div>
                                    )}
                                </div>
                                {!run.ai_analysis && (
                                    <p className="text-sm text-indigo-500">
                                        No analysis yet — pick a provider and hit Analyze failures.
                                    </p>
                                )}
                                {run.ai_analysis && (() => {
                                    const analysis: any = run.ai_analysis;
                                    // Typed RunFailureAnalysis (schema_version >= 1) vs legacy freeform text
                                    if (typeof analysis === 'object' && analysis?.schema_version) {
                                        const categoryStyles: Record<string, string> = {
                                            app_bug: 'bg-pastel-peach text-pastel-ink-peach border-transparent',
                                            test_bug: 'bg-pastel-butter text-pastel-ink-butter border-transparent',
                                            environment: 'bg-pastel-lavender text-pastel-ink-lavender border-transparent',
                                            flake: 'bg-pastel-butter text-pastel-ink-butter border-transparent',
                                            unknown: 'bg-secondary text-ink-soft border-transparent',
                                        };
                                        return (
                                            <div className="space-y-3">
                                                <p className="text-sm text-indigo-900 leading-relaxed">{analysis.summary}</p>
                                                {(analysis.reports || []).map((r: any, i: number) => (
                                                    <div key={i} className="space-y-1.5 rounded-2xl bg-white/80 p-4">
                                                        <div className="flex items-center gap-2 flex-wrap">
                                                            <span className={`text-[11px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border ${categoryStyles[r.root_cause_category] || categoryStyles.unknown}`}>
                                                                {(r.root_cause_category || 'unknown').replace('_', ' ')}
                                                            </span>
                                                            {typeof r.confidence === 'number' && (
                                                                <span className="text-[11px] font-mono text-indigo-400">{Math.round(r.confidence * 100)}% confidence</span>
                                                            )}
                                                            {r.analyzed_by && (
                                                                <span className="text-[11px] font-mono text-indigo-300">{r.analyzed_by}</span>
                                                            )}
                                                        </div>
                                                        {r.test_name && (
                                                            <p className="text-[11px] font-mono text-indigo-400 truncate" title={r.test_name}>{r.test_name}</p>
                                                        )}
                                                        <p className="text-sm font-semibold text-indigo-900">{r.summary}</p>
                                                        {r.details && <p className="text-xs text-indigo-700">{r.details}</p>}
                                                        {r.suggested_fix && (
                                                            <p className="rounded-xl bg-pastel-mint px-3 py-2 text-xs text-pastel-ink-mint">
                                                                <span className="font-bold">Suggested fix{r.fix_target && r.fix_target !== 'none' ? ` (${r.fix_target})` : ''}:</span> {r.suggested_fix}
                                                            </p>
                                                        )}
                                                        {isInvestigation(r) && (
                                                            <div className="pt-2 mt-1 border-t border-indigo-100">
                                                                <InvestigationReport
                                                                    report={r}
                                                                    onSelectEvidence={(ref) => {
                                                                        if (typeof r.result_id !== 'number') return;
                                                                        setTestSearchTerm('');
                                                                        setEvidenceFocus({
                                                                            resultId: r.result_id,
                                                                            stepIndex: stepIndexOfRef(ref),
                                                                            ref,
                                                                            nonce: Date.now(),
                                                                        });
                                                                    }}
                                                                />
                                                            </div>
                                                        )}
                                                    </div>
                                                ))}
                                            </div>
                                        );
                                    }
                                    return (
                                        <div className="prose prose-sm prose-indigo max-w-none text-indigo-900 leading-relaxed">
                                            {typeof analysis === 'string' ? analysis : JSON.stringify(analysis, null, 2)}
                                        </div>
                                    );
                                })()}
                            </div>
                        )}

                        {run.results && run.results.length > 0 && (
                            <ResultsSplit runId={run.id} results={run.results} networkEvents={run.network_events || []}
                                search={testSearchTerm} onSearch={setTestSearchTerm} focus={evidenceFocus} />
                        )}
                    </Tabs.Content>

                    <Tabs.Content value="media" className="focus:outline-none space-y-6">
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* Individual Traces Accordions */}
                            {hasIndividualTraces && (
                                <div className="col-span-full space-y-4">
                                    <h3 className="font-bold text-gray-800 text-lg flex items-center gap-2 mb-4">
                                        <Activity size={20} className="text-primary" /> Test Case Traces
                                    </h3>
                                    {run.results?.filter(r => r.trace_url).map(result => (
                                        <GlobalTraceAccordion key={result.id} result={result} executionLog={run.execution_log} />
                                    ))}
                                </div>
                            )}

                            {traceUrl && !hasIndividualTraces && (
                                <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm lg:col-span-2">
                                    <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 flex items-center justify-between">
                                        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
                                            <Activity size={16} className="text-primary" /> Trace Timeline
                                        </h3>
                                        <a
                                            href={traceUrl}
                                            download
                                            className="text-primary hover:text-primary/80 font-medium text-xs flex items-center gap-1 bg-primary/10 px-3 py-1.5 rounded-full transition-colors"
                                            target="_blank"
                                            rel="noreferrer"
                                        >
                                            <FileText size={14} /> Download
                                        </a>
                                    </div>
                                    <div className="p-4">
                                        <TraceTimeline url={traceUrl} executionLog={run.execution_log} />
                                    </div>
                                </div>
                            )}

                            {videoUrl && !hasIndividualVideos && (
                                <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm h-fit">
                                    <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 font-semibold text-gray-800 flex items-center gap-2">
                                        <Video size={16} className="text-primary" /> Global Recording
                                    </div>
                                    <video controls className="w-full bg-black aspect-video" src={videoUrl} />
                                </div>
                            )}

                            {harUrl && (
                                <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm h-fit">
                                    <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 flex items-center justify-between">
                                        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
                                            <Globe size={16} className="text-primary" /> Network Archive (HAR)
                                        </h3>
                                        <a
                                            href={harUrl}
                                            download
                                            className="text-primary hover:text-primary/80 font-medium text-xs flex items-center gap-1 bg-primary/10 px-3 py-1.5 rounded-full transition-colors"
                                            target="_blank"
                                            rel="noreferrer"
                                        >
                                            <FileText size={14} /> Download .har
                                        </a>
                                    </div>
                                    <p className="px-5 py-4 text-sm text-gray-500">
                                        Full request/response archive of this run — open it in Chrome DevTools
                                        (Network → import) or any HAR viewer.
                                    </p>
                                </div>
                            )}

                            {/* Screenshots Section */}
                            {((run.screenshots && run.screenshots.length > 0) || (run.results && run.results.some(r => r.screenshots && r.screenshots.length > 0))) && (
                                <div className="col-span-full bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm mt-2">
                                    <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 font-semibold text-gray-800 flex items-center gap-2">
                                        <LayoutGrid size={16} className="text-primary" /> Screenshots
                                    </div>
                                    <div className="p-5 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-5">
                                        {(() => {
                                            const seenPaths = new Set<string>();
                                            const screenshots: React.ReactNode[] = [];

                                            run.results?.forEach(result => {
                                                result.screenshots?.forEach((path, index) => {
                                                    if (!seenPaths.has(path)) {
                                                        seenPaths.add(path);
                                                        screenshots.push(
                                                            <ScreenshotItem key={`res-${result.id}-${index}`} path={path} title={`${result.test_name} - ${index + 1}`} />
                                                        );
                                                    }
                                                });
                                            });

                                            run.screenshots?.forEach((path, index) => {
                                                if (!seenPaths.has(path)) {
                                                    seenPaths.add(path);
                                                    screenshots.push(
                                                        <ScreenshotItem key={`run-${index}`} path={path} title={`Run Screenshot ${index + 1}`} />
                                                    );
                                                }
                                            });

                                            return screenshots;
                                        })()}
                                    </div>
                                </div>
                            )}
                        </div>
                    </Tabs.Content>

                    <Tabs.Content value="network" className="focus:outline-none space-y-6">
                        {(run.network_events && run.network_events.length > 0) ? (
                            <NetworkActivitySection events={run.network_events} />
                        ) : (run.response_status || run.request_headers) ? (
                            <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
                                <h3 className="text-gray-900 font-bold flex items-center gap-2 mb-6 text-lg">
                                    <Globe size={20} className="text-primary" /> Global Network Details
                                </h3>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                    <div className="bg-gray-50 rounded-lg p-4 border border-gray-100">
                                        <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Response Status</p>
                                        <p className={`text-2xl font-mono font-bold ${run.response_status && run.response_status >= 400 ? 'text-pastel-ink-peach' : 'text-emerald-600'}`}>
                                            {run.response_status || 'N/A'}
                                        </p>
                                    </div>

                                    {run.request_headers && (
                                        <div className="col-span-full border-t border-gray-100 pt-6">
                                            <div className="flex items-center justify-between w-full mb-3">
                                                <h4 className="flex items-center gap-2 text-sm font-bold text-gray-700 uppercase tracking-wider">
                                                    Request Headers <span className="text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full text-xs">{Object.keys(run.request_headers).length}</span>
                                                </h4>
                                                <CopyButton text={JSON.stringify(run.request_headers, null, 2)} />
                                            </div>
                                            <pre className="text-xs bg-gray-900 text-gray-100 p-4 rounded-xl border border-gray-800 overflow-x-auto font-mono shadow-inner max-h-96 custom-scrollbar">
                                                {Object.keys(run.request_headers).length > 0
                                                    ? JSON.stringify(run.request_headers, null, 2)
                                                    : "No request headers captured"}
                                            </pre>
                                        </div>
                                    )}

                                    {run.response_headers && (
                                        <div className="col-span-full border-t border-gray-100 pt-6">
                                            <div className="flex items-center justify-between w-full mb-3">
                                                <h4 className="flex items-center gap-2 text-sm font-bold text-gray-700 uppercase tracking-wider">
                                                    Response Headers <span className="text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full text-xs">{Object.keys(run.response_headers).length}</span>
                                                </h4>
                                                <CopyButton text={JSON.stringify(run.response_headers, null, 2)} />
                                            </div>
                                            <pre className="text-xs bg-gray-900 text-gray-100 p-4 rounded-xl border border-gray-800 overflow-x-auto font-mono shadow-inner max-h-96 custom-scrollbar">
                                                {Object.keys(run.response_headers).length > 0
                                                    ? JSON.stringify(run.response_headers, null, 2)
                                                    : "No response headers captured"}
                                            </pre>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ) : (
                            <div className="text-center text-gray-500 py-12 italic text-sm flex flex-col items-center bg-gray-50 rounded-xl border border-dashed border-gray-200">
                                <Server size={32} className="text-gray-300 mb-3" />
                                No global network activity captured
                            </div>
                        )}
                    </Tabs.Content>
                </div>
            </div>

            {/* Force Complete Dialog */}
            <AnimatePresence>
                {showForceCompleteDialog && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-gray-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                        onClick={() => setShowForceCompleteDialog(false)}
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0, y: 10 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.95, opacity: 0, y: 10 }}
                            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl border border-gray-100"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="w-12 h-12 rounded-full bg-pastel-peach/60 flex items-center justify-center mb-4">
                                <AlertTriangle size={24} className="text-pastel-ink-peach" />
                            </div>
                            <h3 className="text-xl font-bold text-gray-900 mb-2">Force Complete Test Run?</h3>
                            <p className="text-gray-500 text-sm mb-6 leading-relaxed">
                                This will mark the test run as ERROR and stop waiting for completion. This action cannot be undone.
                            </p>
                            <div className="flex gap-3 justify-end">
                                <button
                                    onClick={() => setShowForceCompleteDialog(false)}
                                    className="px-5 py-2.5 rounded-lg font-medium text-gray-700 hover:bg-gray-100 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={() => forceCompleteMutation.mutate()}
                                    disabled={forceCompleteMutation.isPending}
                                    className="px-5 py-2.5 bg-rose-600 text-white rounded-lg font-medium hover:bg-rose-700 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                                >
                                    {forceCompleteMutation.isPending ? <Activity size={16} className="animate-spin" /> : null}
                                    {forceCompleteMutation.isPending ? "Processing..." : "Force Complete"}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Compare Deployment Dialog */}
            <AnimatePresence>
                {showCompareDialog && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-gray-900/60 backdrop-blur-sm flex items-center justify-center z-50 p-4"
                        onClick={() => setShowCompareDialog(false)}
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0, y: 10 }}
                            animate={{ scale: 1, opacity: 1, y: 0 }}
                            exit={{ scale: 0.95, opacity: 0, y: 10 }}
                            className="bg-white rounded-2xl p-6 max-w-md w-full shadow-2xl border border-gray-100"
                            onClick={(e) => e.stopPropagation()}
                        >
                            <div className="w-12 h-12 rounded-full bg-indigo-50 flex items-center justify-center mb-4">
                                <GitCompareArrows size={24} className="text-indigo-600" />
                            </div>
                            <h3 className="text-xl font-bold text-gray-900 mb-2">Compare Deployment</h3>
                            <p className="text-gray-500 text-sm mb-4 leading-relaxed">
                                Re-runs this run's suite against a different deployment URL (same browser
                                and device) and shows a side-by-side regression diff.
                            </p>
                            <input
                                type="url"
                                autoFocus
                                placeholder="https://staging.example.com"
                                value={compareTargetUrl}
                                onChange={(e) => setCompareTargetUrl(e.target.value)}
                                onKeyDown={(e) => {
                                    if (e.key === 'Enter' && compareTargetUrl.trim()) compareMutation.mutate();
                                }}
                                className="w-full h-11 rounded-lg border border-gray-200 px-3 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/30 mb-6"
                            />
                            <div className="flex gap-3 justify-end">
                                <button
                                    onClick={() => setShowCompareDialog(false)}
                                    className="px-5 py-2.5 rounded-lg font-medium text-gray-700 hover:bg-gray-100 transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={() => compareMutation.mutate()}
                                    disabled={compareMutation.isPending || !compareTargetUrl.trim()}
                                    className="px-5 py-2.5 bg-indigo-600 text-white rounded-lg font-medium hover:bg-indigo-700 transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                                >
                                    {compareMutation.isPending ? <Activity size={16} className="animate-spin" /> : <GitCompareArrows size={16} />}
                                    {compareMutation.isPending ? "Starting…" : "Run Comparison"}
                                </button>
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </Tabs.Root>
    );
}

function NetworkActivitySection({ events }: { events: any[] }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');

    // Group events by testCaseName or testCaseId
    const groupedEvents = events.reduce((acc: any, event: any) => {
        const key = event.testCaseName || event.testCaseId || 'Global / Setup';
        if (!acc[key]) acc[key] = [];
        acc[key].push(event);
        return acc;
    }, {});

    // Filter events based on search
    const filterEvents = (events: any[]) => {
        if (!searchTerm) return events;
        const lowerTerm = searchTerm.toLowerCase();
        return events.filter(e =>
            e.url.toLowerCase().includes(lowerTerm) ||
            e.method.toLowerCase().includes(lowerTerm) ||
            String(e.status).includes(lowerTerm)
        );
    };

    return (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
            <div className="flex items-center justify-between mb-3">
                <button
                    onClick={() => setIsExpanded(!isExpanded)}
                    className="flex items-center gap-2 text-gray-800 font-semibold hover:text-primary transition-colors"
                >
                    <FileText size={18} />
                    Network Activity ({events.length})
                    {isExpanded ? <ChevronDown size={20} /> : <ChevronRight size={20} />}
                </button>

                {isExpanded && (
                    <input
                        type="text"
                        placeholder="Filter requests..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="px-3 py-1 text-sm border rounded-md w-64 focus:outline-none focus:ring-1 focus:ring-primary"
                    />
                )}
            </div>

            {isExpanded && (
                <div className="space-y-4 animate-in slide-in-from-top-2 fade-in duration-200">
                    {Object.entries(groupedEvents).map(([groupKey, groupEvents]: [string, any]) => {
                        const filteredGroupEvents = filterEvents(groupEvents);
                        if (filteredGroupEvents.length === 0) return null;

                        return (
                            <NetworkGroup
                                key={groupKey}
                                title={groupKey}
                                events={filteredGroupEvents}
                                defaultExpanded={false} // Collapsed by default
                            />
                        );
                    })}
                    {Object.values(groupedEvents).every((g: any) => filterEvents(g).length === 0) && (
                        <div className="text-center text-gray-500 py-4 italic">No matching requests found</div>
                    )}
                </div>
            )}
        </div>
    );
}

function NetworkGroup({ title, events, defaultExpanded = false }: { title: string, events: any[], defaultExpanded?: boolean }) {
    const [isExpanded, setIsExpanded] = useState(defaultExpanded);

    return (
        <div className="border border-gray-200 rounded-lg bg-white overflow-hidden">
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full flex items-center justify-between p-3 bg-gray-50 hover:bg-gray-100 transition-colors text-left"
            >
                <div className="flex items-center gap-2">
                    {isExpanded ? <ChevronDown size={16} className="text-gray-500" /> : <ChevronRight size={16} className="text-gray-500" />}
                    <span className="font-medium text-sm text-gray-700">{title}</span>
                    <span className="text-xs text-gray-400 bg-white px-2 py-0.5 rounded border border-gray-200">
                        {events.length}
                    </span>
                </div>
            </button>

            {isExpanded && (
                <div className="p-3 space-y-3 border-t border-gray-200">
                    {events.map((event: any, index: number) => (
                        <NetworkEventItem key={index} event={event} />
                    ))}
                </div>
            )}
        </div>
    );
}

function NetworkEventItem({ event }: { event: any, index?: number }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [showReqHeaders, setShowReqHeaders] = useState(false);
    const [showRespHeaders, setShowRespHeaders] = useState(false);

    const isError = event.status >= 400;
    const duration = event.duration ? `${Math.round(event.duration)}ms` : 'N/A';

    return (
        <div className={`border rounded-lg overflow-hidden ${isError ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-white'}`}>
            <div
                className="p-3 flex items-center justify-between cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-3 overflow-hidden">
                    <span className={`text-xs font-bold px-2 py-1 rounded ${event.method === 'GET' ? 'bg-blue-100 text-blue-700' :
                        event.method === 'POST' ? 'bg-green-100 text-green-700' :
                            event.method === 'PUT' ? 'bg-orange-100 text-orange-700' :
                                event.method === 'DELETE' ? 'bg-red-100 text-red-700' :
                                    'bg-gray-100 text-gray-700'
                        }`}>
                        {event.method}
                    </span>
                    <span className="font-mono text-sm truncate" title={event.url}>
                        {event.url}
                    </span>
                </div>
                <div className="flex items-center gap-4 shrink-0">
                    <span className="text-xs text-gray-500 flex items-center gap-1">
                        <span className="font-medium">{duration}</span>
                    </span>
                    <span className={`text-sm font-bold ${isError ? 'text-red-600' : 'text-green-600'}`}>
                        {event.status}
                    </span>
                    {isExpanded ? <ChevronDown size={16} className="text-gray-400" /> : <ChevronRight size={16} className="text-gray-400" />}
                </div>
            </div>

            {isExpanded && (
                <div className="border-t border-gray-100 p-3 space-y-3 bg-white">
                    {/* Request Headers */}
                    {event.requestHeaders && (
                        <div>
                            <div className="flex items-center justify-between w-full mb-1">
                                <button
                                    onClick={(e) => { e.stopPropagation(); setShowReqHeaders(!showReqHeaders); }}
                                    className="flex items-center gap-2 text-xs font-medium text-gray-600 hover:text-primary transition-colors"
                                >
                                    {showReqHeaders ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                    <span>Request Headers ({Object.keys(event.requestHeaders).length})</span>
                                </button>
                                {showReqHeaders && <CopyButton text={JSON.stringify(event.requestHeaders, null, 2)} />}
                            </div>
                            {showReqHeaders && (
                                <pre className="text-[10px] bg-gray-50 p-2 rounded border overflow-x-auto font-mono text-gray-700">
                                    {JSON.stringify(event.requestHeaders, null, 2)}
                                </pre>
                            )}
                        </div>
                    )}

                    {/* Response Headers */}
                    {event.responseHeaders && (
                        <div>
                            <div className="flex items-center justify-between w-full mb-1">
                                <button
                                    onClick={(e) => { e.stopPropagation(); setShowRespHeaders(!showRespHeaders); }}
                                    className="flex items-center gap-2 text-xs font-medium text-gray-600 hover:text-primary transition-colors"
                                >
                                    {showRespHeaders ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                    <span>Response Headers ({Object.keys(event.responseHeaders).length})</span>
                                </button>
                                {showRespHeaders && <CopyButton text={JSON.stringify(event.responseHeaders, null, 2)} />}
                            </div>
                            {showRespHeaders && (
                                <pre className="text-[10px] bg-gray-50 p-2 rounded border overflow-x-auto font-mono text-gray-700">
                                    {JSON.stringify(event.responseHeaders, null, 2)}
                                </pre>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

function unwrapJson(str: string) {
    try {
        const parsed = JSON.parse(str);
        return JSON.stringify(parsed, null, 2);
    } catch {
        return str;
    }
}

// Direction A: the run's tests as a list, with the selected one shown in full
// on the right (failed tests first, so the one you came for is already open).
function ResultsSplit({ runId, results, networkEvents, search, onSearch, focus = null }: {
    runId: number; results: any[]; networkEvents: any[]; search: string; onSearch: (v: string) => void; focus?: EvidenceFocus | null;
}) {
    const [selectedId, setSelectedId] = useState<number | null>(null);
    // An evidence reference was clicked in the analysis panel: select the
    // cited result (adjusted during render, React's pattern for state derived
    // from a prop change).
    const [seenFocus, setSeenFocus] = useState<number | null>(null);
    if (focus && focus.nonce !== seenFocus) {
        setSeenFocus(focus.nonce);
        setSelectedId(focus.resultId);
    }
    const rank = (r: any) => (r.status === 'failed' || r.status === 'error' ? 0 : r.status === 'passed' ? 2 : 1);
    const shown = results
        .filter((r) => r.test_name.toLowerCase().includes(search.toLowerCase()))
        .sort((a, b) => rank(a) - rank(b));
    const selected = shown.find((r) => r.id === selectedId) ?? shown[0] ?? null;
    return (
        <SplitView
            list={
                <ListCard title="Tests in this run" count={results.length}>
                    <label className="flex items-center gap-2 px-4 py-2.5">
                        <span className="sr-only">Search tests in this run</span>
                        <input type="search" placeholder="Search tests in this run" value={search} onChange={(e) => onSearch(e.target.value)}
                            className="h-10 min-w-0 flex-1 rounded-full border border-input bg-white px-4 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ink/20" />
                    </label>
                    {shown.length === 0 ? (
                        <p className="px-4 py-6 text-center text-sm text-ink-soft">No matching tests.</p>
                    ) : shown.map((r) => (
                        <ListRow
                            key={r.id}
                            selected={selected?.id === r.id}
                            onSelect={() => setSelectedId(r.id)}
                            leading={<KindChip kind={r.status === 'passed' ? 'pass' : (r.status === 'failed' || r.status === 'error') ? 'failure' : 'skip'} size="sm" />}
                            title={r.test_name}
                            meta={`${r.status} · ${Math.round(r.duration_ms)}ms`}
                        />
                    ))}
                </ListCard>
            }
            detail={selected && (
                <TestCaseResultItem
                    key={selected.id}
                    variant="detail"
                    result={selected}
                    runId={runId}
                    networkEvents={networkEvents.filter((e: any) => e.testCaseName === selected.test_name)}
                    focus={focus?.resultId === selected.id ? focus : null}
                />
            )}
        />
    );
}

function TestCaseResultItem({ result, runId, networkEvents = [], variant = 'accordion', focus = null }: { result: any, runId: number, networkEvents?: any[], variant?: 'accordion' | 'detail', focus?: EvidenceFocus | null }) {
    const [expandedState, setIsExpanded] = useState(false);
    const isDetail = variant === 'detail';
    const isExpanded = isDetail || expandedState;
    const stepEvidence = stepEvidenceOf(result);

    // An evidence reference was clicked in the analysis panel: open this
    // result (adjusted during render) and bring the cited step — or, for
    // non-step refs, the result — into view once it has been laid out.
    const [seenFocus, setSeenFocus] = useState<number | null>(null);
    if (focus && focus.nonce !== seenFocus) {
        setSeenFocus(focus.nonce);
        setIsExpanded(true);
    }
    useEffect(() => {
        if (!focus) return;
        const target = focus.stepIndex !== null
            ? stepAnchorId(result.id, focus.stepIndex) : `result-${result.id}`;
        const timer = window.setTimeout(() => {
            document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 250);
        return () => window.clearTimeout(timer);
    }, [focus, result.id]);

    const { data: testVideoUrl } = useQuery({
        queryKey: ["video", result.video_url],
        queryFn: () => getArtifactUrl(result.video_url),
        enabled: !!result.video_url && isExpanded,
    });

    const { data: testTraceUrl } = useQuery({
        queryKey: ["trace", result.trace_url],
        queryFn: () => getArtifactUrl(result.trace_url),
        enabled: !!result.trace_url && isExpanded,
    });

    const isError = result.status === 'failed' || result.status === 'error';

    return (
        <div id={`result-${result.id}`} className={cn(
            "group transition-all duration-300 bg-white scroll-mt-24",
            isDetail ? "overflow-hidden rounded-3xl border border-border shadow-[0_12px_40px_rgba(28,27,34,0.06)]"
                : isExpanded ? "m-4 rounded-xl border border-gray-200 shadow-sm" : "border-b border-gray-100 last:border-0 hover:bg-gray-50/80"
        )}>
            <div
                className={cn(
                    "flex items-center justify-between p-4",
                    isDetail ? "px-6 pt-6 pb-2" : "cursor-pointer select-none",
                    isExpanded && !isDetail && "bg-gray-50/50 rounded-t-xl"
                )}
                onClick={isDetail ? undefined : () => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-4 flex-1 min-w-0 pr-4">
                    <div className={cn(
                        "w-2.5 h-2.5 rounded-full shadow-sm shrink-0",
                        result.status === 'passed' ? "bg-emerald-500 shadow-emerald-200" :
                            isError ? "bg-rose-500 shadow-rose-200" :
                                "bg-gray-400"
                    )} />
                    <span className={cn(
                        "font-semibold truncate",
                        isDetail ? "text-[22px] font-bold tracking-[-0.02em] text-ink whitespace-normal" : "text-sm",
                        !isDetail && (isExpanded ? "text-primary" : "text-gray-800")
                    )} title={result.test_name}>
                        {result.test_name}
                    </span>
                </div>
                <div className="flex items-center gap-6 shrink-0">
                    <span className="text-gray-400 font-mono text-xs hidden sm:block">
                        {Math.round(result.duration_ms)}ms
                    </span>
                    <span className={cn(
                        "font-bold uppercase text-[10px] tracking-wider px-3 py-1 rounded-full border",
                        result.status === 'passed' ? "bg-emerald-50 text-emerald-700 border-emerald-100" :
                            isError ? "bg-pastel-peach/60 text-pastel-ink-peach border-transparent" :
                                "bg-gray-50 text-gray-700 border-gray-200"
                    )}>
                        {result.status}
                    </span>
                    {!isDetail && (
                        <div className={cn(
                            "w-8 h-8 rounded-full flex items-center justify-center transition-transform duration-300",
                            isExpanded ? "bg-white shadow-sm rotate-180 text-primary border border-gray-200" : "text-gray-400 group-hover:text-gray-600"
                        )}>
                            <ChevronDown size={18} />
                        </div>
                    )}
                </div>
            </div>

            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        transition={{ duration: 0.2, ease: "easeInOut" }}
                        className="overflow-hidden border-t border-gray-100"
                    >
                        <div className="p-5 space-y-6">
                            <div className="flex justify-end">
                                <ReproBundleButton runId={runId} resultId={result.id} />
                            </div>
                            {result.error_message && (
                                <div className="bg-pastel-peach/60 border border-transparent rounded-xl p-4">
                                    <p className="text-[10px] font-bold text-pastel-ink-peach mb-2 uppercase tracking-wider flex items-center gap-1.5">
                                        <Bug size={14} /> Error Details
                                    </p>
                                    <pre className="text-xs text-pastel-ink-peach whitespace-pre-wrap font-mono leading-relaxed custom-scrollbar max-h-48 overflow-y-auto">
                                        {result.error_message}
                                    </pre>
                                </div>
                            )}

                            <RetryAttempts attempts={attemptsOf(result as any)} />

                            {(() => {
                                const hookOutcomes = hookOutcomesOf(result.result_payload);
                                return hookOutcomes ? <HookOutcomes outcomes={hookOutcomes} /> : null;
                            })()}

                            <StepEvidenceTimeline
                                resultId={result.id}
                                steps={stepEvidence}
                                highlightedStep={focus?.stepIndex ?? null}
                                highlightedRef={focus?.ref ?? null}
                            />

                            {(result as any).result_kind === 'load' && (result as any).result_payload && (() => {
                                const lp = (result as any).result_payload;
                                const m = lp.metrics || {};
                                const dur = m.http_req_duration || {};
                                const fmt = (v: any, unit = '') => v == null ? '—' : `${Math.round((v + Number.EPSILON) * 100) / 100}${unit}`;
                                const stat = (label: string, value: string) => (
                                    <div key={label} className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                                        <div className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">{label}</div>
                                        <div className="text-lg font-extrabold tabular-nums text-slate-800">{value}</div>
                                    </div>
                                );
                                return (
                                    <div>
                                        <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
                                            Load test · {lp.vus} VUs · {lp.duration_s}s · {lp.target_url}
                                        </p>
                                        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                                            {stat('Requests', fmt(m.http_reqs?.count))}
                                            {stat('RPS', fmt(m.http_reqs?.rate))}
                                            {stat('p95', fmt(dur['p(95)'], 'ms'))}
                                            {stat('p99', fmt(dur['p(99)'] ?? dur['p(99.9)'], 'ms'))}
                                            {stat('Avg', fmt(dur.avg, 'ms'))}
                                            {stat('Error rate', fmt((m.http_req_failed?.value ?? m.http_req_failed?.rate ?? 0) * 100, '%'))}
                                        </div>
                                        {(lp.thresholds || []).length > 0 && (
                                            <div className="flex flex-wrap gap-2 mt-3">
                                                {lp.thresholds.map((t: any) => (
                                                    <span key={t.name} className={cn(
                                                        "px-2.5 py-1 rounded-full border text-[11px] font-semibold tabular-nums",
                                                        t.passed ? "bg-emerald-50 text-emerald-700 border-emerald-200" : "bg-pastel-peach/60 text-pastel-ink-peach border-transparent"
                                                    )}>
                                                        {t.name}: {t.actual == null ? 'n/a' : Math.round((t.actual + Number.EPSILON) * 1000) / 1000} / limit {t.limit}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                        <ServerMetricsPanel runId={result.test_run_id} resultId={result.id} />
                                    </div>
                                );
                            })()}

                            {(result as any).result_payload?.web_vitals && (() => {
                                const wv = (result as any).result_payload.web_vitals;
                                const chip = (label: string, val: number | null, unit: string, warn: boolean) => val == null ? null : (
                                    <span key={label} className={cn(
                                        "px-2.5 py-1 rounded-full border text-[11px] font-semibold tabular-nums",
                                        warn ? "bg-amber-50 text-amber-700 border-amber-200" : "bg-slate-50 text-slate-600 border-slate-200"
                                    )}>
                                        {label} {unit === '' ? val.toFixed(3) : `${Math.round(val)}${unit}`}
                                    </span>
                                );
                                return (
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Web vitals</span>
                                        {chip('TTFB', wv.ttfb_ms, 'ms', wv.ttfb_ms > 800)}
                                        {chip('FCP', wv.fcp_ms, 'ms', wv.fcp_ms > 1800)}
                                        {chip('LCP', wv.lcp_ms, 'ms', wv.lcp_ms > 2500)}
                                        {chip('CLS', wv.cls, '', wv.cls > 0.1)}
                                        {chip('Load', wv.load_ms, 'ms', wv.load_ms > 5000)}
                                    </div>
                                );
                            })()}

                            <Tabs.Root defaultValue="details" className="flex flex-col">
                                <Tabs.List className="flex shrink-0 border-b border-gray-200 mb-5 overflow-x-auto custom-scrollbar pb-px">
                                    <Tabs.Trigger
                                        value="details"
                                        className="px-4 py-2 text-xs font-bold text-gray-500 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none whitespace-nowrap"
                                    >
                                        Details & Body
                                    </Tabs.Trigger>
                                    <Tabs.Trigger
                                        value="headers"
                                        className="px-4 py-2 text-xs font-bold text-gray-500 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none whitespace-nowrap"
                                    >
                                        Headers
                                    </Tabs.Trigger>
                                    {(testVideoUrl || testTraceUrl || (result.screenshots?.length > 0)) && (
                                        <Tabs.Trigger
                                            value="media"
                                            className="px-4 py-2 text-xs font-bold text-gray-500 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none whitespace-nowrap"
                                        >
                                            Media & Traces
                                        </Tabs.Trigger>
                                    )}
                                    {networkEvents.length > 0 && (
                                        <Tabs.Trigger
                                            value="network"
                                            className="px-4 py-2 text-xs font-bold text-gray-500 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider flex items-center gap-1.5 outline-none whitespace-nowrap"
                                        >
                                            Network <span className="bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded-full text-[9px]">{networkEvents.length}</span>
                                        </Tabs.Trigger>
                                    )}
                                </Tabs.List>

                                <Tabs.Content value="details" className="focus:outline-none space-y-6">
                                    <div className="flex flex-wrap gap-6 items-start bg-gray-50/50 p-4 rounded-xl border border-gray-100">
                                        {result.response_status && (
                                            <div>
                                                <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Status</p>
                                                <p className={cn("text-justify font-mono font-bold text-lg", result.response_status >= 400 ? 'text-pastel-ink-peach' : 'text-emerald-600')}>
                                                    {result.response_status}
                                                </p>
                                            </div>
                                        )}
                                        {result.request_method && (
                                            <div>
                                                <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Method</p>
                                                <p className="text-sm font-mono font-bold text-gray-700 bg-white px-2 py-1 rounded shadow-sm border border-gray-200">{result.request_method}</p>
                                            </div>
                                        )}
                                        {result.request_url && (
                                            <div className="flex-1 min-w-[200px]">
                                                <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Request URL</p>
                                                <p className="text-xs font-mono text-gray-600 break-all bg-white p-2 rounded shadow-sm border border-gray-200">{result.request_url}</p>
                                            </div>
                                        )}
                                    </div>

                                    {result.response_body && (() => {
                                        // Check if this is AMP validation data
                                        try {
                                            const ampData = JSON.parse(result.response_body);
                                            if (ampData?.type === 'amp-validate' && ampData?.amp_status) {
                                                const isPass = ampData.amp_status === 'PASS';
                                                return (
                                                    <div className="space-y-4">
                                                        {/* AMP Summary Card */}
                                                        <div className={`flex items-center gap-4 p-4 rounded-xl border ${isPass ? 'bg-emerald-50 border-emerald-200' : 'bg-pastel-peach/60 border-transparent'}`}>
                                                            <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${isPass ? 'bg-emerald-100' : 'bg-rose-100'}`}>
                                                                <Zap size={24} className={isPass ? 'text-emerald-600' : 'text-pastel-ink-peach'} />
                                                            </div>
                                                            <div className="flex-1">
                                                                <div className="flex items-center gap-2">
                                                                    <span className={`text-lg font-bold ${isPass ? 'text-emerald-700' : 'text-pastel-ink-peach'}`}>
                                                                        AMP {ampData.amp_status}
                                                                    </span>
                                                                    {isPass ? <CheckCircle size={18} className="text-emerald-500" /> : <XCircle size={18} className="text-rose-500" />}
                                                                </div>
                                                                <p className="text-xs text-gray-500 font-mono mt-0.5 truncate" title={ampData.url}>{ampData.url}</p>
                                                            </div>
                                                            <div className="flex gap-4 shrink-0">
                                                                {ampData.error_count > 0 && (
                                                                    <div className="text-center">
                                                                        <p className="text-2xl font-bold text-pastel-ink-peach">{ampData.error_count}</p>
                                                                        <p className="text-[10px] font-bold text-rose-400 uppercase">Errors</p>
                                                                    </div>
                                                                )}
                                                                {ampData.warning_count > 0 && (
                                                                    <div className="text-center">
                                                                        <p className="text-2xl font-bold text-amber-600">{ampData.warning_count}</p>
                                                                        <p className="text-[10px] font-bold text-amber-400 uppercase">Warnings</p>
                                                                    </div>
                                                                )}
                                                                {ampData.error_count === 0 && ampData.warning_count === 0 && (
                                                                    <div className="text-center">
                                                                        <p className="text-2xl font-bold text-emerald-600">✓</p>
                                                                        <p className="text-[10px] font-bold text-emerald-400 uppercase">Clean</p>
                                                                    </div>
                                                                )}
                                                            </div>
                                                        </div>

                                                        {/* Errors List */}
                                                        {ampData.errors?.length > 0 && (
                                                            <div className="space-y-2">
                                                                <span className="text-[10px] font-bold text-rose-500 uppercase tracking-wider">Errors ({ampData.errors.length})</span>
                                                                <div className="space-y-1.5 max-h-80 overflow-y-auto custom-scrollbar pr-1">
                                                                    {ampData.errors.map((issue: any, i: number) => (
                                                                        <div key={`err-${i}`} className="flex items-start gap-3 p-3 bg-pastel-peach/60/50 border border-transparent rounded-lg text-sm">
                                                                            <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[9px] font-bold uppercase bg-rose-100 text-pastel-ink-peach rounded">ERR</span>
                                                                            <div className="flex-1 min-w-0">
                                                                                <p className="text-gray-800 text-xs leading-relaxed">{issue.message}</p>
                                                                                <div className="flex items-center gap-3 mt-1.5">
                                                                                    {(issue.line > 0 || issue.col > 0) && (
                                                                                        <span className="text-[10px] font-mono text-gray-400">Line {issue.line}:{issue.col}</span>
                                                                                    )}
                                                                                    {issue.code && <span className="text-[10px] font-mono text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{issue.code}</span>}
                                                                                    {issue.specUrl && (
                                                                                        <a href={issue.specUrl} target="_blank" rel="noreferrer" className="text-[10px] text-violet-500 hover:text-violet-700 flex items-center gap-0.5 transition-colors">
                                                                                            <ExternalLink size={10} /> Spec
                                                                                        </a>
                                                                                    )}
                                                                                </div>
                                                                            </div>
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        )}

                                                        {/* Warnings List */}
                                                        {ampData.warnings?.length > 0 && (
                                                            <div className="space-y-2">
                                                                <span className="text-[10px] font-bold text-amber-500 uppercase tracking-wider">Warnings ({ampData.warnings.length})</span>
                                                                <div className="space-y-1.5 max-h-60 overflow-y-auto custom-scrollbar pr-1">
                                                                    {ampData.warnings.map((issue: any, i: number) => (
                                                                        <div key={`warn-${i}`} className="flex items-start gap-3 p-3 bg-amber-50/50 border border-amber-100 rounded-lg text-sm">
                                                                            <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[9px] font-bold uppercase bg-amber-100 text-amber-700 rounded">WARN</span>
                                                                            <div className="flex-1 min-w-0">
                                                                                <p className="text-gray-800 text-xs leading-relaxed">{issue.message}</p>
                                                                                <div className="flex items-center gap-3 mt-1.5">
                                                                                    {(issue.line > 0 || issue.col > 0) && (
                                                                                        <span className="text-[10px] font-mono text-gray-400">Line {issue.line}:{issue.col}</span>
                                                                                    )}
                                                                                    {issue.code && <span className="text-[10px] font-mono text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">{issue.code}</span>}
                                                                                    {issue.specUrl && (
                                                                                        <a href={issue.specUrl} target="_blank" rel="noreferrer" className="text-[10px] text-violet-500 hover:text-violet-700 flex items-center gap-0.5 transition-colors">
                                                                                            <ExternalLink size={10} /> Spec
                                                                                        </a>
                                                                                    )}
                                                                                </div>
                                                                            </div>
                                                                        </div>
                                                                    ))}
                                                                </div>
                                                            </div>
                                                        )}
                                                    </div>
                                                );
                                            }
                                        } catch { /* Not JSON or not AMP data, fall through */ }

                                        // Default: show raw response body
                                        return (
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-between w-full">
                                                    <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Response Body</span>
                                                    <CopyButton text={unwrapJson(result.response_body)} />
                                                </div>
                                                <pre className="text-xs bg-[#0d1117] text-gray-100 p-4 rounded-xl border border-gray-800 overflow-x-auto font-mono shadow-inner max-h-96 custom-scrollbar">
                                                    {unwrapJson(result.response_body)}
                                                </pre>
                                            </div>
                                        );
                                    })()}

                                    {result.request_body && (
                                        <div className="space-y-2">
                                            <div className="flex items-center justify-between w-full">
                                                <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Request Body</span>
                                                <CopyButton text={unwrapJson(result.request_body)} />
                                            </div>
                                            <pre className="text-xs bg-[#0d1117] text-gray-100 p-4 rounded-xl border border-gray-800 overflow-x-auto font-mono shadow-inner max-h-96 custom-scrollbar">
                                                {unwrapJson(result.request_body)}
                                            </pre>
                                        </div>
                                    )}
                                    {result.request_params && Object.keys(result.request_params).length > 0 && (
                                        <div className="space-y-2">
                                            <div className="flex items-center justify-between w-full">
                                                <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Query Parameters</span>
                                                <CopyButton text={JSON.stringify(result.request_params, null, 2)} />
                                            </div>
                                            <pre className="text-[10px] bg-white p-3 rounded-xl border border-gray-200 overflow-x-auto font-mono text-gray-700 shadow-sm max-h-48">
                                                {JSON.stringify(result.request_params, null, 2)}
                                            </pre>
                                        </div>
                                    )}
                                </Tabs.Content>

                                <Tabs.Content value="headers" className="focus:outline-none space-y-6">
                                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                        {result.request_headers && Object.keys(result.request_headers).length > 0 && (
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-between w-full">
                                                    <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Request Headers</span>
                                                    <CopyButton text={JSON.stringify(result.request_headers, null, 2)} />
                                                </div>
                                                <pre className="text-[10px] bg-white p-4 rounded-xl border border-gray-200 overflow-x-auto font-mono text-gray-700 shadow-sm max-h-96 custom-scrollbar">
                                                    {JSON.stringify(result.request_headers, null, 2)}
                                                </pre>
                                            </div>
                                        )}

                                        {result.response_headers && Object.keys(result.response_headers).length > 0 && (
                                            <div className="space-y-2">
                                                <div className="flex items-center justify-between w-full">
                                                    <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Response Headers</span>
                                                    <CopyButton text={JSON.stringify(result.response_headers, null, 2)} />
                                                </div>
                                                <pre className="text-[10px] bg-white p-4 rounded-xl border border-gray-200 overflow-x-auto font-mono text-gray-700 shadow-sm max-h-96 custom-scrollbar">
                                                    {JSON.stringify(result.response_headers, null, 2)}
                                                </pre>
                                            </div>
                                        )}
                                    </div>
                                </Tabs.Content>

                                <Tabs.Content value="media" className="focus:outline-none space-y-6">
                                    {testTraceUrl && (
                                        <div className="border border-gray-200 rounded-xl overflow-hidden shadow-sm bg-white">
                                            <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 flex items-center justify-between">
                                                <h3 className="font-semibold text-gray-800 flex items-center gap-2">
                                                    <Activity size={16} className="text-primary" /> Trace Timeline
                                                </h3>
                                                <a
                                                    href={testTraceUrl}
                                                    download
                                                    className="text-primary hover:text-primary/80 font-medium text-xs flex items-center gap-1 bg-primary/10 px-3 py-1.5 rounded-full transition-colors"
                                                    target="_blank"
                                                    rel="noreferrer"
                                                >
                                                    <FileText size={14} /> Download
                                                </a>
                                            </div>
                                            <div className="p-4">
                                                <TraceTimeline url={testTraceUrl} />
                                            </div>
                                        </div>
                                    )}
                                    {testVideoUrl && (
                                        <div className="space-y-2">
                                            <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Test Recording</span>
                                            <div className="border border-gray-200 rounded-xl overflow-hidden bg-black shadow-sm">
                                                <video controls className="w-full max-h-[500px]" src={testVideoUrl} />
                                            </div>
                                        </div>
                                    )}
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                        {result.screenshots?.map((path: string, i: number) => (
                                            <ScreenshotItem key={i} path={path} title={`Screenshot ${i + 1}`} />
                                        ))}
                                    </div>
                                </Tabs.Content>

                                {networkEvents.length > 0 && (
                                    <Tabs.Content value="network" className="focus:outline-none">
                                        <div className="space-y-3 max-h-[500px] overflow-y-auto custom-scrollbar pr-2">
                                            {networkEvents.map((event: any, index: number) => (
                                                <NetworkEventItem key={`net-${index}`} event={event} />
                                            ))}
                                        </div>
                                    </Tabs.Content>
                                )}
                            </Tabs.Root>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}

function ScreenshotItem({ path, title }: { path: string, title: string }) {
    const { data: url } = useQuery({
        queryKey: ["screenshot", path],
        queryFn: () => getArtifactUrl(path),
        enabled: !!path,
    });

    if (!url) return <div className="animate-pulse bg-gray-200 h-48 rounded-lg"></div>;

    return (
        <div className="group relative border rounded-lg overflow-hidden bg-gray-100">
            <img src={url} alt={title} className="w-full h-48 object-cover transition-transform group-hover:scale-105" />
            <div className="absolute bottom-0 left-0 right-0 bg-black/60 text-white text-xs p-2 truncate opacity-0 group-hover:opacity-100 transition-opacity">
                {title}
            </div>
            <a
                href={url}
                target="_blank"
                rel="noreferrer"
                className="absolute top-2 right-2 bg-white/90 p-1.5 rounded-full shadow-sm opacity-0 group-hover:opacity-100 transition-opacity hover:bg-white"
                title="Open full size"
            >
                <ChevronRight size={14} className="rotate-[-45deg]" />
            </a>
        </div>
    );
}

function CopyButton({ text }: { text: string }) {
    const [copied, setCopied] = useState(false);

    const handleCopy = (e: React.MouseEvent) => {
        e.stopPropagation();
        navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <button
            onClick={handleCopy}
            className="flex items-center gap-1.5 px-2 py-1 text-[10px] font-medium text-gray-500 hover:text-primary hover:bg-primary/5 rounded transition-all border border-transparent hover:border-primary/20"
            title="Copy to clipboard"
        >
            {copied ? (
                <>
                    <Check size={12} className="text-green-500" />
                    <span className="text-green-600">Copied!</span>
                </>
            ) : (
                <>
                    <Copy size={12} />
                    <span>Copy</span>
                </>
            )}
        </button>
    );
}

function GlobalTraceAccordion({ result, executionLog }: { result: any, executionLog?: any[] }) {
    const [isExpanded, setIsExpanded] = useState(false);

    // Always call the hook (even if not expanded, the query won't fire until enabled is true)
    const { data: testTraceUrl } = useQuery({
        queryKey: ["trace", result.trace_url],
        queryFn: () => getArtifactUrl(result.trace_url),
        enabled: !!result.trace_url && isExpanded,
    });

    return (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            <button
                onClick={() => setIsExpanded(!isExpanded)}
                className="w-full bg-gray-50 hover:bg-gray-100 border-b border-gray-200 px-5 py-3 flex items-center justify-between transition-colors outline-none"
            >
                <div className="flex items-center gap-3">
                    <div className={cn("w-2.5 h-2.5 rounded-full shadow-sm", result.status === 'passed' ? "bg-emerald-500 shadow-emerald-200" : result.status === 'failed' || result.status === 'error' ? "bg-rose-500 shadow-rose-200" : "bg-gray-400")} />
                    <span className="font-semibold text-gray-800 truncate" title={result.test_name}>{result.test_name}</span>
                </div>
                <div className="flex items-center gap-4 text-gray-500 shrink-0">
                    <span className="text-xs font-mono bg-white px-2 py-1 rounded shadow-sm border border-gray-200">
                        {Math.round(result.duration_ms)}ms
                    </span>
                    {isExpanded ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
                </div>
            </button>

            <AnimatePresence>
                {isExpanded && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden"
                    >
                        <div className="p-4 bg-white border-t border-gray-100">
                            {testTraceUrl ? (
                                <div className="border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                                    <div className="bg-gray-50 border-b border-gray-200 px-5 py-3 flex items-center justify-between">
                                        <h3 className="font-semibold text-gray-800 flex items-center gap-2">
                                            <Activity size={16} className="text-primary" /> Trace Timeline
                                        </h3>
                                        <a href={testTraceUrl} download className="text-primary hover:text-primary/80 font-medium text-xs flex items-center gap-1 bg-primary/10 px-3 py-1.5 rounded-full transition-colors" target="_blank" rel="noreferrer">
                                            <FileText size={14} /> Download
                                        </a>
                                    </div>
                                    <div className="p-4">
                                        <TraceTimeline url={testTraceUrl} executionLog={executionLog} />
                                    </div>
                                </div>
                            ) : (
                                <div className="p-8 text-center text-gray-500 flex flex-col items-center border border-dashed border-gray-200 rounded-xl bg-gray-50/50">
                                    <Activity className="animate-spin mb-3 text-primary" size={24} />
                                    <span>Loading trace file...</span>
                                </div>
                            )}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
