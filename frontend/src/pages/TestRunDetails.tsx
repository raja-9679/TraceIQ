import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { getRun, getArtifactUrl } from "@/lib/api";
import { ArrowLeft, Brain, FileText, Video, ChevronDown, ChevronRight, CheckCircle, XCircle, Copy, Check } from "lucide-react";
import { useState } from "react";
import { TraceTimeline } from "@/components/TraceTimeline";

export default function TestRunDetails() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || "0");
    const isValidRunId = !isNaN(runId) && runId > 0;

    const [showReqHeaders, setShowReqHeaders] = useState(true);
    const [showRespHeaders, setShowRespHeaders] = useState(true);
    const [testSearchTerm, setTestSearchTerm] = useState('');
    const [isTestCasesExpanded, setIsTestCasesExpanded] = useState(false);

    const { data: run, isLoading } = useQuery({
        queryKey: ["run", runId],
        queryFn: () => getRun(runId),
        enabled: isValidRunId,
    });

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

    if (!isValidRunId) return <div className="p-4">Invalid Run ID</div>;
    if (isLoading) return <div className="p-4">Loading...</div>;
    if (!run) return <div className="p-4">Run not found</div>;

    return (
        <div className="space-y-6">
            <Link to="/" className="inline-flex items-center text-gray-500 hover:text-gray-900">
                <ArrowLeft size={16} className="mr-2" />
                Back to Matrix
            </Link>

            <div className="flex justify-between items-start">
                <div>
                    <h2 className="text-3xl font-bold text-gray-900">
                        {run.suite_name || `Run #${run.id}`}
                        {run.test_case_name && (
                            <span className="text-gray-400 font-normal"> › {run.test_case_name}</span>
                        )}
                    </h2>
                    <p className="text-gray-500 mt-1">
                        Status: <span className="font-medium text-gray-900">{run.status}</span>
                    </p>
                </div>
            </div>

            {run.results && run.results.length > 0 && (() => {
                const passedCount = run.results.filter(r => r.status === 'passed').length;
                const failedCount = run.results.filter(r => r.status === 'failed').length;

                return (
                    <div className="bg-white border border-gray-200 rounded-lg overflow-hidden shadow-sm">
                        <div
                            className="p-4 flex items-center justify-between cursor-pointer hover:bg-gray-50 transition-colors"
                            onClick={() => setIsTestCasesExpanded(!isTestCasesExpanded)}
                        >
                            <div className="flex items-center gap-4">
                                <h3 className="text-gray-800 font-semibold flex items-center gap-2">
                                    <FileText size={18} className="text-primary" />
                                    Test Cases ({run.results.length})
                                </h3>
                                <div className="flex items-center gap-3 text-sm">
                                    <span className="flex items-center gap-1 text-green-600 font-medium">
                                        <CheckCircle size={14} /> {passedCount} Passed
                                    </span>
                                    <span className="flex items-center gap-1 text-red-600 font-medium">
                                        <XCircle size={14} /> {failedCount} Failed
                                    </span>
                                </div>
                            </div>
                            <div className="flex items-center gap-2">
                                {isTestCasesExpanded ? <ChevronDown size={20} className="text-gray-400" /> : <ChevronRight size={20} className="text-gray-400" />}
                            </div>
                        </div>

                        {isTestCasesExpanded && (
                            <div className="p-4 border-t border-gray-100">
                                <div className="flex items-center justify-between mb-4">
                                    <div className="text-sm text-gray-500">
                                        Showing {run.results.filter(result => result.test_name.toLowerCase().includes(testSearchTerm.toLowerCase())).length} of {run.results.length} test cases
                                    </div>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            placeholder="Search test cases..."
                                            value={testSearchTerm}
                                            onChange={(e) => setTestSearchTerm(e.target.value)}
                                            className="pl-3 pr-3 py-1.5 text-sm border rounded-md w-64 focus:outline-none focus:ring-1 focus:ring-primary bg-gray-50/50"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-3">
                                    {run.results
                                        .filter(result => result.test_name.toLowerCase().includes(testSearchTerm.toLowerCase()))
                                        .map((result) => (
                                            <TestCaseResultItem key={result.id} result={result} />
                                        ))}
                                    {run.results.filter(result => result.test_name.toLowerCase().includes(testSearchTerm.toLowerCase())).length === 0 && (
                                        <p className="text-center text-gray-500 py-4 italic text-sm">No matching test cases found</p>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                );
            })()}

            {run.error_message && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-4">
                    <h3 className="text-red-800 font-semibold flex items-center gap-2">
                        <FileText size={18} />
                        Error Log
                    </h3>
                    <pre className="mt-2 text-sm text-red-700 whitespace-pre-wrap font-mono bg-red-100/50 p-2 rounded">
                        {run.error_message}
                    </pre>
                </div>
            )}

            {run.ai_analysis && (
                <div className="bg-purple-50 border border-purple-200 rounded-lg p-4">
                    <h3 className="text-purple-800 font-semibold flex items-center gap-2">
                        <Brain size={18} />
                        AI Root Cause Analysis
                    </h3>
                    <p className="mt-2 text-purple-900">{run.ai_analysis}</p>
                </div>
            )}

            {(run.network_events && run.network_events.length > 0) ? (
                <NetworkActivitySection events={run.network_events} />
            ) : (run.response_status || run.request_headers) && (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                    <h3 className="text-gray-800 font-semibold flex items-center gap-2 mb-3">
                        <FileText size={18} />
                        Network Details
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div>
                            <p className="text-sm font-medium text-gray-500">Response Status</p>
                            <p className={`mt-1 font-mono ${run.response_status && run.response_status >= 400 ? 'text-red-600' : 'text-green-600'}`}>
                                {run.response_status || 'N/A'}
                            </p>
                        </div>

                        {run.request_headers && (
                            <div className="col-span-full border-t pt-4">
                                <div className="flex items-center justify-between w-full mb-1">
                                    <button
                                        onClick={() => setShowReqHeaders(!showReqHeaders)}
                                        className="flex items-center gap-2 text-sm font-medium text-gray-700 hover:text-primary transition-colors"
                                    >
                                        {showReqHeaders ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        Request Headers
                                        <span className="text-xs font-normal text-gray-400">({Object.keys(run.request_headers).length} items)</span>
                                    </button>
                                    {showReqHeaders && <CopyButton text={JSON.stringify(run.request_headers, null, 2)} />}
                                </div>
                                {showReqHeaders && (
                                    <pre className="mt-2 text-xs bg-white p-3 rounded border overflow-x-auto font-mono text-gray-800 shadow-inner min-h-[50px]">
                                        {Object.keys(run.request_headers).length > 0
                                            ? JSON.stringify(run.request_headers, null, 2)
                                            : "No request headers captured"}
                                    </pre>
                                )}
                            </div>
                        )}

                        {run.response_headers && (
                            <div className="col-span-full border-t pt-4">
                                <div className="flex items-center justify-between w-full mb-1">
                                    <button
                                        onClick={() => setShowRespHeaders(!showRespHeaders)}
                                        className="flex items-center gap-2 text-sm font-medium text-gray-700 hover:text-primary transition-colors"
                                    >
                                        {showRespHeaders ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                                        Response Headers
                                        <span className="text-xs font-normal text-gray-400">({Object.keys(run.response_headers).length} items)</span>
                                    </button>
                                    {showRespHeaders && <CopyButton text={JSON.stringify(run.response_headers, null, 2)} />}
                                </div>
                                {showRespHeaders && (
                                    <pre className="mt-2 text-xs bg-white p-3 rounded border overflow-x-auto font-mono text-gray-800 shadow-inner min-h-[50px]">
                                        {Object.keys(run.response_headers).length > 0
                                            ? JSON.stringify(run.response_headers, null, 2)
                                            : "No response headers captured"}
                                    </pre>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {traceUrl && (
                    <div className="space-y-4">
                        <div className="flex justify-between items-center">
                            <h3 className="font-semibold text-gray-900">Trace Timeline</h3>
                            <a
                                href={traceUrl}
                                download
                                className="text-blue-600 hover:text-blue-800 text-sm flex items-center gap-1"
                                target="_blank"
                                rel="noreferrer"
                            >
                                <FileText size={16} />
                                Download Full Trace
                            </a>
                        </div>
                        <TraceTimeline url={traceUrl} executionLog={run.execution_log} />
                    </div>
                )}

                {videoUrl && (
                    <div className="border border-gray-200 rounded-lg overflow-hidden shadow-sm h-fit">
                        <div className="bg-gray-50 border-b border-gray-200 px-4 py-2 font-medium text-sm text-gray-700 flex items-center gap-2">
                            <Video size={16} />
                            Test Recording
                        </div>
                        <video controls className="w-full bg-black" src={videoUrl} />
                    </div>
                )}

                {/* Screenshots Section */}
                {((run.screenshots && run.screenshots.length > 0) || (run.results && run.results.some(r => r.screenshots && r.screenshots.length > 0))) && (
                    <div className="col-span-full border border-gray-200 rounded-lg overflow-hidden shadow-sm mt-6">
                        <div className="bg-gray-50 border-b border-gray-200 px-4 py-2 font-medium text-sm text-gray-700 flex items-center gap-2">
                            <FileText size={16} />
                            Screenshots
                        </div>
                        <div className="p-4 grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                            {(() => {
                                const seenPaths = new Set<string>();
                                const screenshots: React.ReactNode[] = [];

                                // Result Level Screenshots (Prioritize these as they have test names)
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

                                // Run Level Screenshots
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

function TestCaseResultItem({ result }: { result: any }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [showReqHeaders, setShowReqHeaders] = useState(false);
    const [showRespHeaders, setShowRespHeaders] = useState(false);
    const [showRespBody, setShowRespBody] = useState(true);

    const hasDetails = result.response_status || result.response_body || (result.response_headers && Object.keys(result.response_headers).length > 0);

    return (
        <div className={`border rounded-lg overflow-hidden transition-all duration-200 ${isExpanded ? 'border-primary shadow-md' : 'border-gray-200 hover:border-gray-300 bg-white'}`}>
            <div
                className={`flex items-center justify-between p-3 cursor-pointer transition-colors ${isExpanded ? 'bg-primary/5' : 'bg-gray-50/50 hover:bg-gray-50'}`}
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div className="flex items-center gap-3 min-w-0 flex-1 mr-4">
                    <span className={`w-2.5 h-2.5 rounded-full shrink-0 shadow-sm ${result.status === 'passed' ? 'bg-green-500' :
                        result.status === 'failed' ? 'bg-red-500' :
                            'bg-gray-400'
                        }`} />
                    <span className="font-semibold text-sm text-gray-800 truncate" title={result.test_name}>{result.test_name}</span>
                </div>
                <div className="flex items-center gap-4 text-sm shrink-0">
                    <span className="text-gray-500 font-mono text-xs">{Math.round(result.duration_ms)}ms</span>
                    <span className={`font-bold uppercase text-[10px] tracking-wider px-2 py-0.5 rounded ${result.status === 'passed' ? 'bg-green-100 text-green-700' :
                        result.status === 'failed' ? 'bg-red-100 text-red-700' :
                            'bg-gray-100 text-gray-700'
                        }`}>
                        {result.status}
                    </span>
                    {isExpanded ? <ChevronDown size={18} className="text-primary" /> : <ChevronRight size={18} className="text-gray-400" />}
                </div>
            </div>

            {isExpanded && (
                <div className="p-4 border-t border-gray-100 space-y-4 animate-in slide-in-from-top-2 duration-200 bg-white">
                    {result.error_message && (
                        <div className="bg-red-50 border border-red-100 rounded-lg p-3">
                            <p className="text-[10px] font-bold text-red-800 mb-1 uppercase tracking-wider">Error Details</p>
                            <pre className="text-xs text-red-700 whitespace-pre-wrap font-mono leading-relaxed">{result.error_message}</pre>
                        </div>
                    )}

                    {hasDetails ? (
                        <div className="space-y-4">
                            <div className="flex flex-wrap gap-6 items-start">
                                {result.response_status && (
                                    <div>
                                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Status</p>
                                        <p className={`text-sm font-mono font-bold ${result.response_status >= 400 ? 'text-red-600' : 'text-green-600'}`}>
                                            {result.response_status}
                                        </p>
                                    </div>
                                )}
                                {result.request_method && (
                                    <div>
                                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Method</p>
                                        <p className="text-sm font-mono font-bold text-gray-700">{result.request_method}</p>
                                    </div>
                                )}
                                {result.request_url && (
                                    <div className="flex-1 min-w-[200px]">
                                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Request URL</p>
                                        <p className="text-xs font-mono text-gray-600 break-all bg-gray-50 p-1.5 rounded border border-gray-100">{result.request_url}</p>
                                    </div>
                                )}
                            </div>

                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                {/* Request Headers */}
                                {result.request_headers && Object.keys(result.request_headers).length > 0 && (
                                    <div className="space-y-1">
                                        <div className="flex items-center justify-between w-full mb-1">
                                            <button
                                                onClick={() => setShowReqHeaders(!showReqHeaders)}
                                                className="flex items-center gap-2 text-[10px] font-bold text-gray-500 hover:text-primary transition-colors uppercase tracking-wider"
                                            >
                                                {showReqHeaders ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                                <span>Request Headers ({Object.keys(result.request_headers).length})</span>
                                            </button>
                                            {showReqHeaders && <CopyButton text={JSON.stringify(result.request_headers, null, 2)} />}
                                        </div>
                                        {showReqHeaders && (
                                            <pre className="text-[10px] bg-gray-50 p-2.5 rounded-lg border border-gray-200 overflow-x-auto font-mono text-gray-700 shadow-inner max-h-48">
                                                {JSON.stringify(result.request_headers, null, 2)}
                                            </pre>
                                        )}
                                    </div>
                                )}

                                {/* Response Headers */}
                                {result.response_headers && Object.keys(result.response_headers).length > 0 && (
                                    <div className="space-y-1">
                                        <div className="flex items-center justify-between w-full mb-1">
                                            <button
                                                onClick={() => setShowRespHeaders(!showRespHeaders)}
                                                className="flex items-center gap-2 text-[10px] font-bold text-gray-500 hover:text-primary transition-colors uppercase tracking-wider"
                                            >
                                                {showRespHeaders ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                                <span>Response Headers ({Object.keys(result.response_headers).length})</span>
                                            </button>
                                            {showRespHeaders && <CopyButton text={JSON.stringify(result.response_headers, null, 2)} />}
                                        </div>
                                        {showRespHeaders && (
                                            <pre className="text-[10px] bg-gray-50 p-2.5 rounded-lg border border-gray-200 overflow-x-auto font-mono text-gray-700 shadow-inner max-h-48">
                                                {JSON.stringify(result.response_headers, null, 2)}
                                            </pre>
                                        )}
                                    </div>
                                )}
                            </div>

                            {/* Response Body */}
                            {result.response_body && (
                                <div className="space-y-1">
                                    <div className="flex items-center justify-between w-full mb-1">
                                        <button
                                            onClick={() => setShowRespBody(!showRespBody)}
                                            className="flex items-center gap-2 text-[10px] font-bold text-gray-500 hover:text-primary transition-colors uppercase tracking-wider"
                                        >
                                            {showRespBody ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                            <span>Response Body</span>
                                        </button>
                                        {showRespBody && <CopyButton text={(() => {
                                            try {
                                                const parsed = JSON.parse(result.response_body);
                                                return JSON.stringify(parsed, null, 2);
                                            } catch (e) {
                                                return result.response_body;
                                            }
                                        })()} />}
                                    </div>
                                    {showRespBody && (
                                        <pre className="text-xs bg-gray-900 text-gray-100 p-4 rounded-lg border border-gray-800 overflow-x-auto font-mono shadow-lg max-h-96 custom-scrollbar">
                                            {(() => {
                                                try {
                                                    const parsed = JSON.parse(result.response_body);
                                                    return JSON.stringify(parsed, null, 2);
                                                } catch (e) {
                                                    return result.response_body;
                                                }
                                            })()}
                                        </pre>
                                    )}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex flex-col items-center justify-center py-6 text-gray-400 bg-gray-50/50 rounded-lg border border-dashed border-gray-200">
                            <FileText size={24} className="mb-2 opacity-20" />
                            <p className="text-xs italic">No additional network details captured for this test case.</p>
                        </div>
                    )}
                </div>
            )}
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
