import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api, triggerRun } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Plus, Play, FolderOpen, FileText } from 'lucide-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

export default function TestSuites() {
    const queryClient = useQueryClient();
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [newExecutionMode, setNewExecutionMode] = useState<'continuous' | 'separate'>('continuous');
    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');

    const { data: suites, isLoading } = useQuery({
        queryKey: ['suites'],
        queryFn: () => api.get('/suites').then(res => res.data.filter((s: any) => !s.parent_id))
    });

    const filteredSuites = suites?.filter((suite: any) =>
        suite.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (suite.description && suite.description.toLowerCase().includes(searchTerm.toLowerCase()))
    );

    const createSuite = useMutation({
        mutationFn: (data: { name: string; description?: string; execution_mode: string }) => api.post('/suites', data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            setNewSuiteName('');
            setNewSuiteDesc('');
            setShowCreateDialog(false);
        }
    });

    const navigate = useNavigate();

    const runMutation = useMutation({
        mutationFn: (id: number) => triggerRun(id),
        onSuccess: () => {
            navigate('/runs');
        },
        onError: (error) => {
            console.error("Failed to start run:", error);
            // Optional: Add toast notification here
        }
    });

    const handleCreate = () => {
        if (newSuiteName.trim()) {
            createSuite.mutate({
                name: newSuiteName,
                description: newSuiteDesc || undefined,
                execution_mode: newExecutionMode
            });
        }
    };

    if (isLoading) return <div className="p-8">Loading suites...</div>;

    return (
        <div className="space-y-6">
            <div className="flex justify-between items-center">
                <div>
                    <h1 className="text-3xl font-bold text-gray-900">Test Suites</h1>
                    <p className="text-gray-500 mt-1">Organize and manage your test collections</p>
                </div>
                <div className="flex gap-4">
                    <input
                        type="text"
                        placeholder="Search suites..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary w-64"
                    />
                    <Button onClick={() => setShowCreateDialog(true)}>
                        <Plus className="mr-2 h-4 w-4" /> Create Suite
                    </Button>
                </div>
            </div>

            {/* Create Suite Dialog */}
            {showCreateDialog && (
                <Card className="border-2 border-primary">
                    <CardHeader>
                        <CardTitle>Create New Test Suite</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                                Suite Name *
                            </label>
                            <input
                                type="text"
                                value={newSuiteName}
                                onChange={(e) => setNewSuiteName(e.target.value)}
                                placeholder="e.g., Smoke Tests, Regression Suite"
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                                Description
                            </label>
                            <textarea
                                value={newSuiteDesc}
                                onChange={(e) => setNewSuiteDesc(e.target.value)}
                                placeholder="Brief description of this test suite..."
                                rows={3}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                                Execution Mode
                            </label>
                            <select
                                value={newExecutionMode}
                                onChange={(e) => setNewExecutionMode(e.target.value as any)}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                            >
                                <option value="continuous">Continuous (One run for all cases)</option>
                                <option value="separate">Separate (Individual run for each case)</option>
                            </select>
                            <p className="text-xs text-gray-500 mt-1">
                                {newExecutionMode === 'continuous'
                                    ? "All test cases will run in a single browser session with one video."
                                    : "Each test case will start a fresh browser session with its own video and logs."}
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Button onClick={handleCreate} disabled={!newSuiteName.trim()}>
                                Create Suite
                            </Button>
                            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                                Cancel
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Suites Grid */}
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
                {filteredSuites?.map((suite: any) => (
                    <Card key={suite.id} className="hover:shadow-lg transition-shadow group">
                        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-2">
                            <div className="flex items-center space-x-2">
                                <FolderOpen className="h-5 w-5 text-primary" />
                                <CardTitle className="text-lg font-semibold">
                                    {suite.name}
                                </CardTitle>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {suite.description && (
                                <p className="text-sm text-gray-600">{suite.description}</p>
                            )}
                            <div className="flex items-center justify-between text-sm">
                                <div className="flex items-center text-gray-500">
                                    <FileText className="h-4 w-4 mr-1" />
                                    <span>{suite.total_test_cases || 0} test cases</span>
                                </div>
                                {suite.total_sub_modules > 0 && (
                                    <div className="flex items-center text-gray-500">
                                        <FolderOpen className="h-4 w-4 mr-1" />
                                        <span>{suite.total_sub_modules} modules</span>
                                    </div>
                                )}
                                {(!suite.sub_modules || suite.sub_modules.length === 0) && (
                                    <div className="flex items-center text-gray-500">
                                        <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold ${suite.execution_mode === 'separate' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'}`}>
                                            {suite.execution_mode}
                                        </span>
                                    </div>
                                )}
                                <span className="text-xs text-gray-400">
                                    {new Date(suite.created_at).toLocaleDateString()}
                                </span>
                            </div>
                            <div className="flex gap-2 pt-2">
                                <Link to={`/suites/${suite.id}`} className="flex-1">
                                    <Button variant="outline" size="sm" className="w-full">
                                        View Cases
                                    </Button>
                                </Link>
                                <Button
                                    size="sm"
                                    className="flex-1"
                                    onClick={() => runMutation.mutate(suite.id)}
                                    disabled={runMutation.isPending}
                                >
                                    <Play className="mr-1 h-3 w-3" />
                                    {runMutation.isPending ? 'Starting...' : 'Run'}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                ))}
            </div>

            {(!suites || suites.length === 0) && !showCreateDialog && (
                <Card className="p-12 text-center">
                    <FolderOpen className="h-16 w-16 mx-auto text-gray-300 mb-4" />
                    <h3 className="text-lg font-medium text-gray-900 mb-2">No test suites yet</h3>
                    <p className="text-gray-500 mb-6">Get started by creating your first test suite</p>
                    <Button onClick={() => setShowCreateDialog(true)}>
                        <Plus className="mr-2 h-4 w-4" /> Create Your First Suite
                    </Button>
                </Card>
            )}
        </div>
    );
}

