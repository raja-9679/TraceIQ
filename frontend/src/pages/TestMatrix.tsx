import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getRuns, triggerRun } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Play, Eye, Clock, CheckCircle2, XCircle, AlertCircle } from "lucide-react";
import { Link } from "react-router-dom";

export default function TestMatrix() {
    const queryClient = useQueryClient();

    const { data: runs, isLoading } = useQuery({
        queryKey: ["runs"],
        queryFn: getRuns,
        refetchInterval: 2000,
    });

    const triggerMutation = useMutation({
        mutationFn: (suiteId: number) => triggerRun(suiteId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["runs"] });
        },
    });

    const getStatusIcon = (status: string) => {
        switch (status) {
            case 'passed': return <CheckCircle2 className="h-5 w-5 text-green-600" />;
            case 'failed': return <XCircle className="h-5 w-5 text-red-600" />;
            case 'running': return <Clock className="h-5 w-5 text-blue-600 animate-spin" />;
            case 'error': return <AlertCircle className="h-5 w-5 text-orange-600" />;
            default: return <Clock className="h-5 w-5 text-gray-400" />;
        }
    };

    const getStatusColor = (status: string) => {
        switch (status) {
            case 'passed': return 'bg-green-50 text-green-700 border-green-200';
            case 'failed': return 'bg-red-50 text-red-700 border-red-200';
            case 'running': return 'bg-blue-50 text-blue-700 border-blue-200';
            case 'error': return 'bg-orange-50 text-orange-700 border-orange-200';
            default: return 'bg-gray-50 text-gray-700 border-gray-200';
        }
    };

    if (isLoading) return <div>Loading...</div>;

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900">Test Runs</h1>
                    <p className="text-gray-500 mt-1">View and manage all test executions</p>
                </div>
                <Button onClick={() => triggerMutation.mutate(1)} disabled={triggerMutation.isPending}>
                    <Play className="mr-2 h-4 w-4" />
                    {triggerMutation.isPending ? "Starting..." : "Run Test Suite"}
                </Button>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle>All Test Runs</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="overflow-x-auto">
                        <table className="w-full">
                            <thead>
                                <tr className="border-b border-gray-200">
                                    <th className="text-left py-3 px-4 font-medium text-gray-700">ID</th>
                                    <th className="text-left py-3 px-4 font-medium text-gray-700">Status</th>
                                    <th className="text-left py-3 px-4 font-medium text-gray-700">Duration</th>
                                    <th className="text-left py-3 px-4 font-medium text-gray-700">Created At</th>
                                    <th className="text-left py-3 px-4 font-medium text-gray-700">Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {runs?.map((run) => (
                                    <tr key={run.id} className="border-b border-gray-100 hover:bg-gray-50">
                                        <td className="py-4 px-4">
                                            <span className="font-mono text-sm">#{run.id}</span>
                                        </td>
                                        <td className="py-4 px-4">
                                            <div className="flex items-center space-x-2">
                                                {getStatusIcon(run.status)}
                                                <span className={`px-2 py-1 rounded-full text-xs font-medium border ${getStatusColor(run.status)}`}>
                                                    {run.status}
                                                </span>
                                            </div>
                                        </td>
                                        <td className="py-4 px-4 text-sm text-gray-600">
                                            {run.duration_ms ? `${(run.duration_ms / 1000).toFixed(2)}s` : "-"}
                                        </td>
                                        <td className="py-4 px-4 text-sm text-gray-600">
                                            {new Date(run.created_at).toLocaleString()}
                                        </td>
                                        <td className="py-4 px-4">
                                            <Link to={`/runs/${run.id}`}>
                                                <Button variant="outline" size="sm">
                                                    <Eye className="mr-1 h-3 w-3" /> View
                                                </Button>
                                            </Link>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {(!runs || runs.length === 0) && (
                            <div className="text-center py-12">
                                <p className="text-gray-500">No test runs yet. Click "Run Test Suite" to get started.</p>
                            </div>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
