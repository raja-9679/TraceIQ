import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { getRun, getArtifactUrl } from "@/lib/api";
import { ArrowLeft, Brain, FileText, Video } from "lucide-react";
import { TraceTimeline } from "@/components/TraceTimeline";

export default function TestRunDetails() {
    const { runId: idParam } = useParams<{ runId: string }>();
    const runId = parseInt(idParam || "0");
    const isValidRunId = !isNaN(runId) && runId > 0;

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
                    <h2 className="text-3xl font-bold text-gray-900">Run #{run.id}</h2>
                    <p className="text-gray-500 mt-1">
                        Status: <span className="font-medium text-gray-900">{run.status}</span>
                    </p>
                </div>
            </div>

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

            {/* Network Details */}
            {(run.response_status || run.request_headers) && (
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
                        {run.response_headers && (
                            <div className="col-span-full">
                                <p className="text-sm font-medium text-gray-500 mb-1">Response Headers</p>
                                <pre className="text-xs bg-white p-2 rounded border overflow-x-auto">
                                    {JSON.stringify(run.response_headers, null, 2)}
                                </pre>
                            </div>
                        )}
                        {run.request_headers && (
                            <div className="col-span-full">
                                <p className="text-sm font-medium text-gray-500 mb-1">Request Headers</p>
                                <pre className="text-xs bg-white p-2 rounded border overflow-x-auto">
                                    {JSON.stringify(run.request_headers, null, 2)}
                                </pre>
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
                        <TraceTimeline url={traceUrl} />
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
            </div>
        </div>
    );
}
