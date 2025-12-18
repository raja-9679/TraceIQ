import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate } from 'react-router-dom';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Plus, Play, Trash2, Edit, ArrowLeft, FileText } from 'lucide-react';
import { useState } from 'react';

export default function SuiteDetails() {
    const { suiteId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [showTriggerDialog, setShowTriggerDialog] = useState(false);
    const [newTestName, setNewTestName] = useState('');
    const [newTestSteps, setNewTestSteps] = useState('');

    const { data: suite, isLoading } = useQuery({
        queryKey: ['suite', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
    });

    const createTestCase = useMutation({
        mutationFn: (data: { name: string; steps: string[] }) =>
            api.post(`/suites/${suiteId}/cases`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setNewTestName('');
            setNewTestSteps('');
            setShowCreateDialog(false);
        },
    });

    const handleCreateTest = () => {
        if (newTestName.trim()) {
            const steps = newTestSteps
                .split('\n')
                .map(s => s.trim())
                .filter(s => s.length > 0);

            createTestCase.mutate({
                name: newTestName,
                steps: steps.length > 0 ? steps : ['Navigate to application', 'Perform test actions', 'Verify results'],
            });
        }
    };

    if (isLoading) return <div>Loading suite...</div>;
    if (!suite) return <div>Suite not found</div>;

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                    <Button variant="ghost" size="sm" onClick={() => navigate('/suites')}>
                        <ArrowLeft className="h-4 w-4 mr-2" />
                        Back to Suites
                    </Button>
                    <div>
                        <h1 className="text-3xl font-bold text-gray-900">{suite.name}</h1>
                        {suite.description && (
                            <p className="text-gray-500 mt-1">{suite.description}</p>
                        )}
                    </div>
                </div>
                <div className="flex gap-2">
                    <Button onClick={() => navigate(`/suites/${suiteId}/builder`)}>
                        <Plus className="mr-2 h-4 w-4" /> New Test Case
                    </Button>
                    <Button onClick={() => triggerRun(Number(suiteId))}>
                        <Play className="mr-2 h-4 w-4" /> Run Suite
                    </Button>
                </div>
            </div>

            {/* Create Test Dialog */}
            {showCreateDialog && (
                <Card className="border-2 border-primary">
                    <CardHeader>
                        <CardTitle>Create New Test Case</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                                Test Name *
                            </label>
                            <input
                                type="text"
                                value={newTestName}
                                onChange={(e) => setNewTestName(e.target.value)}
                                placeholder="e.g., Login Test, Checkout Flow"
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">
                                Test Steps (one per line)
                            </label>
                            <textarea
                                value={newTestSteps}
                                onChange={(e) => setNewTestSteps(e.target.value)}
                                placeholder="Navigate to login page&#10;Enter username&#10;Enter password&#10;Click login button&#10;Verify dashboard is displayed"
                                rows={6}
                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary font-mono text-sm"
                            />
                            <p className="text-xs text-gray-500 mt-1">
                                Enter each step on a new line. Leave empty for default steps.
                            </p>
                        </div>
                        <div className="flex gap-2">
                            <Button onClick={handleCreateTest} disabled={!newTestName.trim()}>
                                Create Test Case
                            </Button>
                            <Button variant="outline" onClick={() => setShowCreateDialog(false)}>
                                Cancel
                            </Button>
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* Test Cases List */}
            <Card>
                <CardHeader>
                    <CardTitle>Test Cases ({suite.test_cases?.length || 0})</CardTitle>
                </CardHeader>
                <CardContent>
                    {suite.test_cases && suite.test_cases.length > 0 ? (
                        <div className="space-y-4">
                            {suite.test_cases.map((testCase: any) => (
                                <div
                                    key={testCase.id}
                                    className="border border-gray-200 rounded-lg p-4 hover:border-primary transition-colors"
                                >
                                    <div className="flex items-start justify-between">
                                        <div className="flex-1">
                                            <div className="flex items-center space-x-2 mb-2">
                                                <FileText className="h-5 w-5 text-primary" />
                                                <h3 className="font-semibold text-lg">{testCase.name}</h3>
                                            </div>
                                            {testCase.steps && testCase.steps.length > 0 && (
                                                <div className="ml-7">
                                                    <p className="text-sm font-medium text-gray-700 mb-2">Steps:</p>
                                                    <ol className="list-decimal list-inside space-y-1">
                                                        {testCase.steps.map((step: any, idx: number) => (
                                                            <li key={idx} className="text-sm text-gray-600">
                                                                {typeof step === 'string' ? (
                                                                    step
                                                                ) : (
                                                                    <span>
                                                                        <span className="font-semibold text-primary">{step.type}</span>
                                                                        {step.selector && <span className="text-gray-500"> on {step.selector}</span>}
                                                                        {step.value && <span className="text-gray-900"> "{step.value}"</span>}
                                                                    </span>
                                                                )}
                                                            </li>
                                                        ))}
                                                    </ol>
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex gap-2">
                                            <Button variant="ghost" size="sm" onClick={() => triggerRun(Number(suiteId), testCase.id)}>
                                                <Play className="h-4 w-4 text-green-600" />
                                            </Button>
                                            <Button variant="ghost" size="sm" onClick={() => navigate(`/suites/${suiteId}/cases/${testCase.id}/edit`)}>
                                                <Edit className="h-4 w-4" />
                                            </Button>
                                            <Button variant="ghost" size="sm" className="text-red-600 hover:text-red-700">
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="text-center py-12">
                            <FileText className="h-16 w-16 mx-auto text-gray-300 mb-4" />
                            <h3 className="text-lg font-medium text-gray-900 mb-2">No test cases yet</h3>
                            <p className="text-gray-500 mb-6">Add your first test case to this suite</p>
                            <Button onClick={() => setShowCreateDialog(true)}>
                                <Plus className="mr-2 h-4 w-4" /> Add Test Case
                            </Button>
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
