import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Plus, Play, FolderOpen, FileText } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

export default function TestSuites() {
    const queryClient = useQueryClient();
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [showCreateDialog, setShowCreateDialog] = useState(false);

    const { data: suites, isLoading } = useQuery({
        queryKey: ['suites'],
        queryFn: () => api.get('/suites').then(res => res.data)
    });

    const createSuite = useMutation({
        mutationFn: (data: { name: string; description?: string }) => api.post('/suites', data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            setNewSuiteName('');
            setNewSuiteDesc('');
            setShowCreateDialog(false);
        }
    });

    const handleCreate = () => {
        if (newSuiteName.trim()) {
            createSuite.mutate({
                name: newSuiteName,
                description: newSuiteDesc || undefined
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
                <Button onClick={() => setShowCreateDialog(true)}>
                    <Plus className="mr-2 h-4 w-4" /> Create Suite
                </Button>
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
                {suites?.map((suite: any) => (
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
                                    <span>{suite.test_cases?.length || 0} test cases</span>
                                </div>
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
                                <Button size="sm" className="flex-1">
                                    <Play className="mr-1 h-3 w-3" /> Run
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


export default function TestSuites() {
    const queryClient = useQueryClient();
    const [newSuiteName, setNewSuiteName] = useState('');
    const [newSuiteDesc, setNewSuiteDesc] = useState('');
    const [showCreateDialog, setShowCreateDialog] = useState(false);

    const { data: suites, isLoading } = useQuery({
        queryKey: ['suites'],
        queryFn: () => api.get('/suites').then(res => res.data)
    });

    const createSuite = useMutation({
        mutationFn: (data: { name: string; description?: string }) => api.post('/suites', data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suites'] });
            setNewSuiteName('');
            setNewSuiteDesc('');
            setShowCreateDialog(false);
        }
    });

    const handleCreate = () => {
        if (newSuiteName.trim()) {
            createSuite.mutate({
                name: newSuiteName,
                description: newSuiteDesc || undefined
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
                <Button onClick={() => setShowCreateDialog(true)}>
                    <Plus className="mr-2 h-4 w-4" /> Create Suite
                </Button>
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
                {suites?.map((suite: any) => (
                    <Card key={suite.id} className="hover:shadow-lg transition-shadow cursor-pointer group">
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
                                    <span>{suite.test_cases?.length || 0} test cases</span>
                                </div>
                                <span className="text-xs text-gray-400">
                                    {new Date(suite.created_at).toLocaleDateString()}
                                </span>
                            </div>
                            <div className="flex gap-2 pt-2">
                                <Button variant="outline" size="sm" className="flex-1">
                                    View Cases
                                </Button>
                                <Button size="sm" className="flex-1">
                                    <Play className="mr-1 h-3 w-3" /> Run
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
