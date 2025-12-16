import { useQuery } from "@tanstack/react-query";
import { useParams, Link } from "react-router-dom";
import { getRun, getArtifactUrl } from "@/lib/api";
import { ArrowLeft, Brain, FileText, Video } from "lucide-react";

export default function TestRunDetails() {
    const { id } = useParams<{ id: string }>();
    const runId = parseInt(id || "0");

    const { data: run, isLoading } = useQuery({
        queryKey: ["run", runId],
        queryFn: () => getRun(runId),
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

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {traceUrl && (
                    <div className="border border-gray-200 rounded-lg overflow-hidden shadow-sm h-[600px]">
                        <div className="bg-gray-50 border-b border-gray-200 px-4 py-2 font-medium text-sm text-gray-700">
                            Trace Viewer
                        </div>
                        <iframe
                            src={`https://trace.playwright.dev/?trace=${encodeURIComponent(traceUrl)}`}
                            className="w-full h-full border-0"
                            title="Playwright Trace Viewer"
                        />
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
