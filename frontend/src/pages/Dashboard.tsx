import { useQuery } from '@tanstack/react-query';
import { getRuns } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { CheckCircle2, XCircle, Clock, TrendingUp } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { useAuth } from '@/context/AuthContext';

export default function Dashboard() {
    const { user } = useAuth();
    const navigate = useNavigate();
    const { data: runs } = useQuery({
        queryKey: ['runs'],
        queryFn: getRuns,
        refetchInterval: 2000,
    });

    const stats = {
        total: runs?.length || 0,
        passed: runs?.filter(r => r.status === 'passed').length || 0,
        failed: runs?.filter(r => r.status === 'failed').length || 0,
        running: runs?.filter(r => r.status === 'running').length || 0,
    };

    const passRate = stats.total > 0 ? ((stats.passed / stats.total) * 100).toFixed(1) : 0;

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold text-gray-900">Welcome back, {user?.full_name?.split(' ')[0] || 'User'}!</h1>
                <p className="text-gray-500 mt-1">Overview of your test automation platform</p>
            </div>

            {/* Stats Grid */}
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Total Runs</CardTitle>
                        <TrendingUp className="h-4 w-4 text-muted-foreground" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold">{stats.total}</div>
                        <p className="text-xs text-muted-foreground mt-1">All test executions</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Passed</CardTitle>
                        <CheckCircle2 className="h-4 w-4 text-green-600" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold text-green-600">{stats.passed}</div>
                        <p className="text-xs text-muted-foreground mt-1">{passRate}% pass rate</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Failed</CardTitle>
                        <XCircle className="h-4 w-4 text-red-600" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold text-red-600">{stats.failed}</div>
                        <p className="text-xs text-muted-foreground mt-1">Needs attention</p>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                        <CardTitle className="text-sm font-medium">Running</CardTitle>
                        <Clock className="h-4 w-4 text-blue-600" />
                    </CardHeader>
                    <CardContent>
                        <div className="text-2xl font-bold text-blue-600">{stats.running}</div>
                        <p className="text-xs text-muted-foreground mt-1">In progress</p>
                    </CardContent>
                </Card>
            </div>

            {/* Recent Runs */}
            <Card>
                <CardHeader>
                    <CardTitle>Recent Test Runs</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        {runs?.slice(0, 5).map((run) => (
                            <div
                                key={run.id}
                                className="flex items-center justify-between p-4 border rounded-lg hover:bg-gray-50 cursor-pointer transition-colors"
                                onClick={() => navigate(`/runs/${run.id}`)}
                            >
                                <div className="flex items-center space-x-4">
                                    <div className={`h-10 w-10 rounded-full flex items-center justify-center ${run.status === 'passed' ? 'bg-green-100' :
                                        run.status === 'failed' ? 'bg-red-100' :
                                            run.status === 'running' ? 'bg-blue-100' : 'bg-gray-100'
                                        }`}>
                                        {run.status === 'passed' && <CheckCircle2 className="h-5 w-5 text-green-600" />}
                                        {run.status === 'failed' && <XCircle className="h-5 w-5 text-red-600" />}
                                        {run.status === 'running' && <Clock className="h-5 w-5 text-blue-600 animate-spin" />}
                                    </div>
                                    <div>
                                        <p className="font-medium">
                                            {run.suite_name || `Run #${run.id}`}
                                            {run.test_case_name && (
                                                <span className="text-gray-400 font-normal"> › {run.test_case_name}</span>
                                            )}
                                        </p>
                                        <p className="text-sm text-gray-500">
                                            {new Date(run.created_at).toLocaleString()}
                                        </p>
                                    </div>
                                </div>
                                <div className="text-right">
                                    <p className="text-sm font-medium capitalize">{run.status}</p>
                                    {run.duration_ms && (
                                        <p className="text-xs text-gray-500">{(run.duration_ms / 1000).toFixed(2)}s</p>
                                    )}
                                </div>
                            </div>
                        ))}
                        {(!runs || runs.length === 0) && (
                            <p className="text-center text-gray-500 py-8">No test runs yet</p>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
