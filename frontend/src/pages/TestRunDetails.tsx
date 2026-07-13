import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { getRun, getArtifactUrl, forceCompleteRun } from "@/lib/api";
import { ArrowLeft, Brain, FileText, Video, ChevronDown, ChevronRight, CheckCircle, XCircle, Copy, Check, AlertTriangle, Clock, Activity, LayoutGrid, Bug, PlayCircle, Layers, Server, Globe, Zap, ExternalLink } from "lucide-react";
import { useState, useEffect } from "react";
import { TraceTimeline } from "@/components/TraceTimeline";
import { toast } from "sonner";
import * as Tabs from "@radix-ui/react-tabs";
import { motion, AnimatePresence } from "framer-motion";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}

export default function TestRunDetails() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || "0");
    const isValidRunId = !isNaN(runId) && runId > 0;
    const queryClient = useQueryClient();

    const [testSearchTerm, setTestSearchTerm] = useState('');
    const [showForceCompleteDialog, setShowForceCompleteDialog] = useState(false);

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

        const baseUrl = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";
        // Convert http(s) to ws(s)
        const wsUrl = baseUrl.replace(/^http/, 'ws') + `/ws/runs/${runId}`;

        console.log("Connecting to WebSocket:", wsUrl);
        const ws = new WebSocket(wsUrl);

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
        <div className="space-y-8 animate-in fade-in duration-500 pb-12">
            {/* Sticky Header */}
            <div className="sticky top-0 z-30 -mx-6 px-6 py-4 bg-white/80 backdrop-blur-md border-b border-gray-200 shadow-sm transition-all">
                <div className="max-w-7xl mx-auto flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div>
                        <div className="flex items-center gap-2 text-sm text-gray-500 mb-2">
                            <Link to="/" className="hover:text-primary flex items-center gap-1 transition-colors">
                                <ArrowLeft size={14} /> Matrix
                            </Link>
                            {run.suite_name && (
                                <>
                                    <span className="text-gray-300">/</span>
                                    <Link to={`/suites/${run.test_suite_id}`} className="hover:text-primary transition-colors">
                                        {run.suite_name}
                                    </Link>
                                </>
                            )}
                            <span className="text-gray-300">/</span>
                            <span className="font-medium text-gray-700">Run #{run.id}</span>
                        </div>
                        <h2 className="text-2xl md:text-3xl font-bold text-gray-900 flex items-center gap-3">
                            {run.test_case_name || run.suite_name || `Run #${run.id}`}
                            <span className={cn(
                                "flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wide border shadow-sm",
                                run.status === 'passed' ? "bg-emerald-50 text-emerald-700 border-emerald-200" :
                                    run.status === 'failed' || run.status === 'error' ? "bg-rose-50 text-rose-700 border-rose-200" :
                                        "bg-amber-50 text-amber-700 border-amber-200 animate-pulse"
                            )}>
                                {run.status === 'passed' && <CheckCircle size={14} />}
                                {(run.status === 'failed' || run.status === 'error') && <XCircle size={14} />}
                                {run.status === 'running' && <Activity size={14} className="animate-spin" />}
                                {run.status}
                            </span>
                        </h2>
                    </div>
                    {isStuckTest && (
                        <div className="flex items-center gap-3">
                            <button
                                onClick={() => setShowForceCompleteDialog(true)}
                                className="px-4 py-2 bg-rose-600 text-white rounded-lg hover:bg-rose-700 transition-all shadow-sm hover:shadow text-sm font-medium flex items-center gap-2"
                            >
                                <AlertTriangle size={16} /> Force Complete
                            </button>
                        </div>
                    )}
                </div>
            </div>

            <div className="max-w-7xl mx-auto space-y-8">
                {/* Metric Summary Cards */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow">
                        <div className="flex items-center gap-3 text-gray-500 mb-2">
                            <Clock size={18} className="text-primary" />
                            <h3 className="text-sm font-semibold uppercase tracking-wider">Duration</h3>
                        </div>
                        <p className="text-3xl font-bold text-gray-900">
                            {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : '---'}
                        </p>
                    </div>

                    <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow">
                        <div className="flex items-center gap-3 text-gray-500 mb-2">
                            <LayoutGrid size={18} className="text-blue-500" />
                            <h3 className="text-sm font-semibold uppercase tracking-wider">Total Tests</h3>
                        </div>
                        <p className="text-3xl font-bold text-gray-900">{totalCount}</p>
                    </div>

                    <div className="bg-white p-5 rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow relative overflow-hidden sm:col-span-2">
                        <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-3 text-gray-500">
                                <Activity size={18} className={passRate === 100 ? "text-emerald-500" : "text-rose-500"} />
                                <h3 className="text-sm font-semibold uppercase tracking-wider">Pass Rate</h3>
                            </div>
                            <span className={cn(
                                "text-sm font-bold",
                                passRate === 100 ? "text-emerald-600" : "text-rose-600"
                            )}>{passRate}%</span>
                        </div>
                        <div className="w-full bg-gray-100 rounded-full h-2 mb-2 mt-4 overflow-hidden flex">
                            {totalCount > 0 && (
                                <>
                                    <div className="bg-emerald-500 h-full transition-all" style={{ width: `${(passedCount / totalCount) * 100}%` }}></div>
                                    <div className="bg-rose-500 h-full transition-all" style={{ width: `${(failedCount / totalCount) * 100}%` }}></div>
                                </>
                            )}
                        </div>
                        <div className="flex justify-between text-xs font-medium text-gray-500">
                            <span className="text-emerald-600">{passedCount} Passed</span>
                            <span className="text-rose-600">{failedCount} Failed</span>
                        </div>
                    </div>
                </div>

                {/* Main Content Tabs */}
                <Tabs.Root defaultValue="overview" className="flex flex-col">
                    <Tabs.List className="flex shrink-0 border-b border-gray-200 mb-6 bg-transparent">
                        <Tabs.Trigger
                            value="overview"
                            className="px-5 py-3 flex items-center gap-2 text-sm font-medium text-gray-600 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none"
                        >
                            <LayoutGrid size={16} /> Overview
                        </Tabs.Trigger>
                        <Tabs.Trigger
                            value="media"
                            className="px-5 py-3 flex items-center gap-2 text-sm font-medium text-gray-600 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none"
                        >
                            <PlayCircle size={16} /> Traces
                        </Tabs.Trigger>
                        <Tabs.Trigger
                            value="network"
                            className="px-5 py-3 flex items-center gap-2 text-sm font-medium text-gray-600 border-b-2 border-transparent hover:text-gray-900 data-[state=active]:border-primary data-[state=active]:text-primary transition-all uppercase tracking-wider outline-none"
                        >
                            <Server size={16} /> Network
                        </Tabs.Trigger>
                    </Tabs.List>

                    <Tabs.Content value="overview" className="focus:outline-none space-y-6">
                        {run.error_message && (
                            <div className="bg-rose-50 border border-rose-200 rounded-xl p-5 shadow-sm">
                                <h3 className="text-rose-800 font-semibold flex items-center gap-2 mb-3">
                                    <Bug size={18} /> Error Log
                                </h3>
                                <pre className="text-sm text-rose-700 whitespace-pre-wrap font-mono bg-white/50 p-4 rounded-lg border border-rose-100/50 shadow-inner">
                                    {run.error_message}
                                </pre>
                            </div>
                        )}

                        {run.ai_analysis && (
                            <div className="bg-gradient-to-r from-indigo-50 to-purple-50 border border-indigo-100 rounded-xl p-5 shadow-sm">
                                <h3 className="text-indigo-800 font-semibold flex items-center gap-2 mb-3">
                                    <Brain size={18} className="text-indigo-600" /> AI Root Cause Analysis
                                </h3>
                                {(() => {
                                    const analysis: any = run.ai_analysis;
                                    // Typed RunFailureAnalysis (schema_version >= 1) vs legacy freeform text
                                    if (typeof analysis === 'object' && analysis?.schema_version) {
                                        const categoryStyles: Record<string, string> = {
                                            app_bug: 'bg-rose-100 text-rose-700 border-rose-200',
                                            test_bug: 'bg-amber-100 text-amber-700 border-amber-200',
                                            environment: 'bg-sky-100 text-sky-700 border-sky-200',
                                            flake: 'bg-slate-100 text-slate-600 border-slate-200',
                                            unknown: 'bg-slate-100 text-slate-500 border-slate-200',
                                        };
                                        return (
                                            <div className="space-y-3">
                                                <p className="text-sm text-indigo-900 leading-relaxed">{analysis.summary}</p>
                                                {(analysis.reports || []).map((r: any, i: number) => (
                                                    <div key={i} className="bg-white/70 border border-indigo-100 rounded-lg p-3 space-y-1.5">
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
                                                        <p className="text-sm font-semibold text-indigo-900">{r.summary}</p>
                                                        {r.details && <p className="text-xs text-indigo-700">{r.details}</p>}
                                                        {r.suggested_fix && (
                                                            <p className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-100 rounded-md px-2 py-1.5">
                                                                <span className="font-bold">Suggested fix{r.fix_target && r.fix_target !== 'none' ? ` (${r.fix_target})` : ''}:</span> {r.suggested_fix}
                                                            </p>
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
                            <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
                                <div className="p-4 border-b border-gray-100 flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-gray-50/50">
                                    <h3 className="text-gray-800 font-bold flex items-center gap-2">
                                        <Layers size={18} className="text-primary" />
                                        Test Cases
                                    </h3>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            placeholder="Search test cases..."
                                            value={testSearchTerm}
                                            onChange={(e) => setTestSearchTerm(e.target.value)}
                                            className="pl-3 pr-3 py-2 text-sm border bg-white rounded-lg w-full sm:w-64 focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary shadow-sm transition-all"
                                        />
                                    </div>
                                </div>
                                <div className="divide-y divide-gray-100">
                                    {run.results
                                        .filter(result => result.test_name.toLowerCase().includes(testSearchTerm.toLowerCase()))
                                        .map((result) => {
                                            const testNetworkEvents = (run.network_events || []).filter(
                                                (event: any) => event.testCaseName === result.test_name
                                            );
                                            return (
                                                <TestCaseResultItem
                                                    key={result.id}
                                                    result={result}
                                                    networkEvents={testNetworkEvents}
                                                />
                                            );
                                        })}
                                    {run.results.filter(result => result.test_name.toLowerCase().includes(testSearchTerm.toLowerCase())).length === 0 && (
                                        <div className="text-center text-gray-500 py-12 italic text-sm flex flex-col items-center">
                                            <FileText size={32} className="text-gray-300 mb-3" />
                                            No matching test cases found
                                        </div>
                                    )}
                                </div>
                            </div>
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
                                        <p className={`text-2xl font-mono font-bold ${run.response_status && run.response_status >= 400 ? 'text-rose-600' : 'text-emerald-600'}`}>
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
                </Tabs.Root>
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
                            <div className="w-12 h-12 rounded-full bg-rose-50 flex items-center justify-center mb-4">
                                <AlertTriangle size={24} className="text-rose-600" />
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
        </div>
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

function TestCaseResultItem({ result, networkEvents = [] }: { result: any, networkEvents?: any[] }) {
    const [isExpanded, setIsExpanded] = useState(false);

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
        <div className={cn(
            "group transition-all duration-300 bg-white",
            isExpanded ? "m-4 rounded-xl border border-gray-200 shadow-sm" : "border-b border-gray-100 last:border-0 hover:bg-gray-50/80"
        )}>
            <div
                className={cn(
                    "flex items-center justify-between p-4 cursor-pointer select-none",
                    isExpanded && "bg-gray-50/50 rounded-t-xl"
                )}
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-4 flex-1 min-w-0 pr-4">
                    <div className={cn(
                        "w-2.5 h-2.5 rounded-full shadow-sm shrink-0",
                        result.status === 'passed' ? "bg-emerald-500 shadow-emerald-200" :
                            isError ? "bg-rose-500 shadow-rose-200" :
                                "bg-gray-400"
                    )} />
                    <span className={cn(
                        "font-semibold text-sm truncate",
                        isExpanded ? "text-primary" : "text-gray-800"
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
                            isError ? "bg-rose-50 text-rose-700 border-rose-100" :
                                "bg-gray-50 text-gray-700 border-gray-200"
                    )}>
                        {result.status}
                    </span>
                    <div className={cn(
                        "w-8 h-8 rounded-full flex items-center justify-center transition-transform duration-300",
                        isExpanded ? "bg-white shadow-sm rotate-180 text-primary border border-gray-200" : "text-gray-400 group-hover:text-gray-600"
                    )}>
                        <ChevronDown size={18} />
                    </div>
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
                            {result.error_message && (
                                <div className="bg-rose-50 border border-rose-100 rounded-xl p-4">
                                    <p className="text-[10px] font-bold text-rose-800 mb-2 uppercase tracking-wider flex items-center gap-1.5">
                                        <Bug size={14} /> Error Details
                                    </p>
                                    <pre className="text-xs text-rose-700 whitespace-pre-wrap font-mono leading-relaxed custom-scrollbar max-h-48 overflow-y-auto">
                                        {result.error_message}
                                    </pre>
                                </div>
                            )}

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
                                                <p className={cn("text-justify font-mono font-bold text-lg", result.response_status >= 400 ? 'text-rose-600' : 'text-emerald-600')}>
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
                                                        <div className={`flex items-center gap-4 p-4 rounded-xl border ${isPass ? 'bg-emerald-50 border-emerald-200' : 'bg-rose-50 border-rose-200'}`}>
                                                            <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${isPass ? 'bg-emerald-100' : 'bg-rose-100'}`}>
                                                                <Zap size={24} className={isPass ? 'text-emerald-600' : 'text-rose-600'} />
                                                            </div>
                                                            <div className="flex-1">
                                                                <div className="flex items-center gap-2">
                                                                    <span className={`text-lg font-bold ${isPass ? 'text-emerald-700' : 'text-rose-700'}`}>
                                                                        AMP {ampData.amp_status}
                                                                    </span>
                                                                    {isPass ? <CheckCircle size={18} className="text-emerald-500" /> : <XCircle size={18} className="text-rose-500" />}
                                                                </div>
                                                                <p className="text-xs text-gray-500 font-mono mt-0.5 truncate" title={ampData.url}>{ampData.url}</p>
                                                            </div>
                                                            <div className="flex gap-4 shrink-0">
                                                                {ampData.error_count > 0 && (
                                                                    <div className="text-center">
                                                                        <p className="text-2xl font-bold text-rose-600">{ampData.error_count}</p>
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
                                                                        <div key={`err-${i}`} className="flex items-start gap-3 p-3 bg-rose-50/50 border border-rose-100 rounded-lg text-sm">
                                                                            <span className="shrink-0 mt-0.5 px-1.5 py-0.5 text-[9px] font-bold uppercase bg-rose-100 text-rose-700 rounded">ERR</span>
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
