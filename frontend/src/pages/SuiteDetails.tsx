import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { api } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Plus, Play, Trash2, Edit, FileText, FolderOpen, Search } from 'lucide-react';
import { useState, useEffect } from 'react';
import { triggerRun } from '@/lib/api';
import { ChevronRight } from 'lucide-react';

export default function SuiteDetails() {
    const { suiteId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    const [showCreateDialog, setShowCreateDialog] = useState(false);
    const [showSubModuleDialog, setShowSubModuleDialog] = useState(false);
    const [newTestName, setNewTestName] = useState('');
    const [newTestSteps, setNewTestSteps] = useState('');
    const [newModuleName, setNewModuleName] = useState('');
    const [newModuleDesc, setNewModuleDesc] = useState('');
    const [activeTab, setActiveTab] = useState<'tests' | 'settings'>('tests');
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);
    const [deleteConfirmName, setDeleteConfirmName] = useState('');
    const [headerKey, setHeaderKey] = useState('');
    const [headerVal, setHeaderVal] = useState('');
    const [paramKey, setParamKey] = useState('');
    const [paramVal, setParamVal] = useState('');
    const [moduleError, setModuleError] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const location = useLocation();

    useEffect(() => {
        if (location.state?.message) {
            setSuccessMessage(location.state.message);
            // Clear state to prevent message from showing again on refresh
            window.history.replaceState({}, document.title);
            const timer = setTimeout(() => setSuccessMessage(null), 3000);
            return () => clearTimeout(timer);
        }
    }, [location]);

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
                steps: steps.length > 0 ? steps : [],
            });
        }
    };

    const createSubModule = useMutation({
        mutationFn: (data: { name: string; description?: string; parent_id: number }) =>
            api.post(`/suites`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setNewModuleName('');
            setNewModuleDesc('');
            setShowSubModuleDialog(false);
            setModuleError(null);
        },
        onError: (error: any) => {
            setModuleError(error.response?.data?.detail || "Failed to create sub-module");
        }
    });

    const handleCreateSubModule = () => {
        if (newModuleName.trim()) {
            createSubModule.mutate({
                name: newModuleName,
                description: newModuleDesc || undefined,
                parent_id: Number(suiteId),
            });
        }
    };

    const deleteSuite = useMutation({
        mutationFn: () => api.delete(`/suites/${suiteId}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite'] });
            const parentId = suite?.parent_id;
            const targetPath = parentId ? `/suites/${parentId}` : '/suites';

            setShowDeleteDialog(false);
            navigate(targetPath, {
                state: { message: "Module deleted successfully" }
            });
        },
    });

    const updateSettings = useMutation({
        mutationFn: (data: any) => api.put(`/suites/${suiteId}`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
        },
    });

    const handleDeleteSuite = () => {
        if (deleteConfirmName === suite.name) {
            deleteSuite.mutate();
        }
    };

    const handleUpdateSettings = (newSettings: any, inherit: boolean) => {
        const settings = newSettings || { headers: {}, params: {} };
        updateSettings.mutate({
            settings: settings,
            inherit_settings: inherit
        });
    };

    const handleRunSuite = async () => {
        try {
            await triggerRun(Number(suiteId));
            navigate('/runs');
        } catch (error) {
            console.error("Failed to start run:", error);
        }
    };

    const handleRunTestCase = async (caseId: number) => {
        try {
            await triggerRun(Number(suiteId), caseId);
            navigate('/runs');
        } catch (error) {
            console.error("Failed to start run:", error);
        }
    };

    if (isLoading) return <div>Loading suite...</div>;
    if (!suite) return <div>Suite not found</div>;

    return (
        <div className="space-y-6">
            {successMessage && (
                <div className="bg-green-50 border border-green-200 text-green-800 px-4 py-3 rounded-lg flex items-center gap-2 animate-in fade-in slide-in-from-top-4">
                    <div className="h-2 w-2 bg-green-500 rounded-full animate-pulse" />
                    {successMessage}
                </div>
            )}
            {/* Header */}
            <div className="flex items-center space-x-4">
                <div className="flex flex-col">
                    <div className="flex items-center text-sm text-gray-500 mb-1">
                        <Button variant="link" size="sm" className="p-0 h-auto text-gray-500 hover:text-primary" onClick={() => navigate('/suites')}>
                            Suites
                        </Button>
                        {suite.parent && (
                            <>
                                <ChevronRight className="h-3 w-3 mx-1" />
                                <Button variant="link" size="sm" className="p-0 h-auto text-gray-500 hover:text-primary" onClick={() => navigate(`/suites/${suite.parent.id}`)}>
                                    {suite.parent.name}
                                </Button>
                            </>
                        )}
                        <ChevronRight className="h-3 w-3 mx-1" />
                        <span className="font-medium text-gray-900">{suite.name}</span>
                    </div>
                    <div className="flex items-center gap-3">
                        <h1 className="text-3xl font-bold text-gray-900">{suite.name}</h1>
                        {(!suite.sub_modules || suite.sub_modules.length === 0) && (
                            <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold ${suite.execution_mode === 'separate' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'}`}>
                                {suite.execution_mode}
                            </span>
                        )}
                    </div>
                    {suite.description && (
                        <p className="text-gray-500 mt-1">{suite.description}</p>
                    )}
                    <div className="flex items-center gap-4 mt-2 text-sm text-gray-500">
                        <div className="flex items-center">
                            <FileText className="h-4 w-4 mr-1" />
                            <span>{suite.total_test_cases || 0} total test cases</span>
                        </div>
                        {suite.total_sub_modules > 0 && (
                            <div className="flex items-center">
                                <FolderOpen className="h-4 w-4 mr-1" />
                                <span>{suite.total_sub_modules} total modules</span>
                            </div>
                        )}
                    </div>
                </div>
                <div className="ml-auto flex gap-2">
                    <Button variant="outline" className="text-red-600 hover:bg-red-50 border-red-200" onClick={() => setShowDeleteDialog(true)}>
                        <Trash2 className="mr-2 h-4 w-4" /> Delete Module
                    </Button>
                </div>
            </div>

            {/* Delete Confirmation Modal */}
            {showDeleteDialog && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <Card className="w-full max-w-md mx-4">
                        <CardHeader>
                            <CardTitle className="text-red-600">Delete Module?</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-gray-600">
                                This will permanently delete <strong>{suite.name}</strong> and all its sub-modules and test cases. This action cannot be undone.
                            </p>
                            <div className="space-y-2">
                                <label className="text-sm font-medium">Type <span className="font-bold">{suite.name}</span> to confirm:</label>
                                <input
                                    type="text"
                                    value={deleteConfirmName}
                                    onChange={(e) => setDeleteConfirmName(e.target.value)}
                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-red-500 outline-none"
                                    placeholder="Enter module name"
                                />
                            </div>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteDialog(false); setDeleteConfirmName(''); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteConfirmName !== suite.name || deleteSuite.isPending}
                                    onClick={handleDeleteSuite}
                                >
                                    {deleteSuite.isPending ? 'Deleting...' : 'Delete Everything'}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Tabs */}
            <div className="flex border-b border-gray-200">
                <button
                    className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'tests' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
                    onClick={() => setActiveTab('tests')}
                >
                    Tests & Modules
                </button>
                <button
                    className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'settings' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
                    onClick={() => setActiveTab('settings')}
                >
                    Settings & Inheritance
                </button>
            </div>

            {activeTab === 'tests' ? (
                <div className="space-y-6">
                    <div className="flex justify-between items-center gap-4">
                        <div className="relative flex-1 max-w-sm">
                            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
                            <input
                                type="text"
                                placeholder="Search modules or test cases..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                            />
                        </div>
                        <div className="flex gap-2">
                            {suite.total_sub_modules === 0 && (
                                <Button onClick={() => navigate(`/suites/${suiteId}/builder`)}>
                                    <Plus className="mr-2 h-4 w-4" /> New Test Case
                                </Button>
                            )}
                            {suite.total_test_cases === 0 && (
                                <Button variant="outline" onClick={() => setShowSubModuleDialog(true)}>
                                    <FolderOpen className="mr-2 h-4 w-4" /> New Sub-Module
                                </Button>
                            )}
                            <Button onClick={handleRunSuite}>
                                <Play className="mr-2 h-4 w-4" /> Run Suite
                            </Button>
                        </div>
                    </div>
                    {/* ... rest of the tests content ... */}

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

                    {/* Sub-Modules List */}
                    {suite.sub_modules && suite.sub_modules.length > 0 && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between">
                                <h2 className="text-xl font-semibold text-gray-900 flex items-center gap-2">
                                    <FolderOpen className="h-5 w-5 text-primary" />
                                    Sub-Modules ({suite.sub_modules.length})
                                </h2>
                                <Button variant="outline" size="sm" onClick={() => setShowSubModuleDialog(true)}>
                                    <Plus className="mr-2 h-4 w-4" /> Add Module
                                </Button>
                            </div>
                            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                                {suite.sub_modules
                                    .filter((sub: any) =>
                                        sub.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                                        (sub.description && sub.description.toLowerCase().includes(searchTerm.toLowerCase()))
                                    )
                                    .map((sub: any) => (
                                        <Card
                                            key={sub.id}
                                            className="hover:border-primary transition-all cursor-pointer hover:shadow-md group bg-white border-gray-200"
                                            onClick={() => navigate(`/suites/${sub.id}`)}
                                        >
                                            <CardContent className="p-4 flex items-center justify-between">
                                                <div className="flex items-center space-x-3">
                                                    <div className="p-2 bg-primary/10 rounded-lg group-hover:bg-primary/20 transition-colors">
                                                        <FolderOpen className="h-5 w-5 text-primary" />
                                                    </div>
                                                    <div>
                                                        <span className="font-semibold text-gray-900 block">{sub.name}</span>
                                                        {sub.description && <p className="text-xs text-gray-500 line-clamp-1">{sub.description}</p>}
                                                    </div>
                                                </div>
                                                <ChevronRight className="h-4 w-4 text-gray-400 group-hover:text-primary transition-colors" />
                                            </CardContent>
                                        </Card>
                                    ))}
                            </div>
                        </div>
                    )}

                    {/* Create Sub-Module Dialog */}
                    {showSubModuleDialog && (
                        <Card className="border-2 border-primary">
                            <CardHeader>
                                <CardTitle>Create New Sub-Module</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                {moduleError && (
                                    <div className="bg-red-50 border border-red-200 text-red-700 px-3 py-2 rounded text-sm">
                                        {moduleError}
                                    </div>
                                )}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">
                                        Module Name *
                                    </label>
                                    <input
                                        type="text"
                                        value={newModuleName}
                                        onChange={(e) => setNewModuleName(e.target.value)}
                                        placeholder="e.g., Auth Module, Payment Flow"
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">
                                        Description
                                    </label>
                                    <textarea
                                        value={newModuleDesc}
                                        onChange={(e) => setNewModuleDesc(e.target.value)}
                                        placeholder="Brief description of this module..."
                                        rows={3}
                                        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                    />
                                </div>
                                <div className="flex gap-2">
                                    <Button onClick={handleCreateSubModule} disabled={!newModuleName.trim()}>
                                        Create Module
                                    </Button>
                                    <Button variant="outline" onClick={() => setShowSubModuleDialog(false)}>
                                        Cancel
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    {/* Test Cases List */}
                    {suite.total_sub_modules === 0 && (
                        <Card>
                            <CardHeader>
                                <CardTitle>Test Cases ({suite.test_cases?.length || 0})</CardTitle>
                            </CardHeader>
                            <CardContent>
                                {suite.test_cases && suite.test_cases.length > 0 ? (
                                    <div className="space-y-4">
                                        {suite.test_cases
                                            .filter((tc: any) => tc.name.toLowerCase().includes(searchTerm.toLowerCase()))
                                            .map((testCase: any) => (
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
                                                            <Button variant="ghost" size="sm" onClick={() => handleRunTestCase(testCase.id)}>
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
                                        {suite.total_sub_modules === 0 && (
                                            <Button onClick={() => setShowCreateDialog(true)}>
                                                <Plus className="mr-2 h-4 w-4" /> Add Test Case
                                            </Button>
                                        )}
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}
                </div>
            ) : (
                <div className="space-y-6">
                    <Card>
                        <CardHeader>
                            <CardTitle>Module Settings</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-6">
                            {suite.parent_id && (
                                <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg border border-gray-200">
                                    <div>
                                        <h3 className="font-semibold text-gray-900">Inherit Settings from Parent</h3>
                                        <p className="text-sm text-gray-500">Automatically use headers and parameters defined in parent modules.</p>
                                    </div>
                                    <Button
                                        variant={suite.inherit_settings ? "default" : "outline"}
                                        onClick={() => handleUpdateSettings(suite.settings, !suite.inherit_settings)}
                                    >
                                        {suite.inherit_settings ? "Inheritance On" : "Inheritance Off"}
                                    </Button>
                                </div>
                            )}

                            {/* Execution Mode Setting */}
                            <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg border border-gray-200">
                                <div>
                                    <h3 className="font-semibold text-gray-900">Execution Mode</h3>
                                    <p className="text-sm text-gray-500">
                                        {suite.execution_mode === 'continuous'
                                            ? "Running all test cases in a single browser session."
                                            : "Running each test case in a separate browser session."}
                                    </p>
                                </div>
                                <div className="flex items-center gap-2">
                                    <select
                                        value={suite.execution_mode}
                                        onChange={(e) => updateSettings.mutate({ execution_mode: e.target.value })}
                                        className="px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-sm"
                                    >
                                        <option value="continuous">Continuous</option>
                                        <option value="separate">Separate</option>
                                    </select>
                                </div>
                            </div>

                            <div className="grid gap-6 md:grid-cols-2">
                                <div className="space-y-4">
                                    <h3 className="font-semibold flex items-center gap-2">
                                        <Edit className="h-4 w-4 text-primary" />
                                        Custom Headers
                                    </h3>
                                    <div className="space-y-2">
                                        {/* Inherited Headers (Read-only) */}
                                        {suite.inherit_settings && suite.effective_settings?.headers &&
                                            Object.entries(suite.effective_settings.headers)
                                                .filter(([key]) => !suite.settings?.headers?.[key]) // Only show if not overridden
                                                .map(([key, value]: [string, any], idx) => (
                                                    <div key={`inherited-${idx}`} className="flex gap-2 opacity-60">
                                                        <input disabled value={key} className="flex-1 px-3 py-1 bg-gray-100 border rounded text-sm italic" />
                                                        <input disabled value={value} className="flex-1 px-3 py-1 bg-gray-100 border rounded text-sm italic" />
                                                        <div className="w-8" /> {/* Spacer for trash icon */}
                                                    </div>
                                                ))}

                                        {/* Custom Headers */}
                                        {Object.entries(suite.settings?.headers || {}).map(([key, value]: [string, any], idx) => (
                                            <div key={idx} className="flex gap-2">
                                                <input disabled value={key} className="flex-1 px-3 py-1 bg-white border rounded text-sm font-medium" />
                                                <input disabled value={value} className="flex-1 px-3 py-1 bg-white border rounded text-sm" />
                                                <Button variant="ghost" size="sm" onClick={() => {
                                                    const newHeaders = { ...suite.settings.headers };
                                                    delete newHeaders[key];
                                                    handleUpdateSettings({ ...suite.settings, headers: newHeaders }, suite.inherit_settings);
                                                }}>
                                                    <Trash2 className="h-4 w-4 text-red-500" />
                                                </Button>
                                            </div>
                                        ))}
                                        <div className="flex gap-2 pt-2">
                                            <input
                                                value={headerKey}
                                                onChange={(e) => setHeaderKey(e.target.value)}
                                                placeholder="Key"
                                                className="flex-1 px-3 py-1 border rounded text-sm"
                                            />
                                            <input
                                                value={headerVal}
                                                onChange={(e) => setHeaderVal(e.target.value)}
                                                placeholder="Value"
                                                className="flex-1 px-3 py-1 border rounded text-sm"
                                            />
                                            <Button size="sm" onClick={() => {
                                                if (headerKey && headerVal) {
                                                    const currentSettings = suite.settings || { headers: {}, params: {} };
                                                    handleUpdateSettings({
                                                        ...currentSettings,
                                                        headers: { ...(currentSettings.headers || {}), [headerKey]: headerVal }
                                                    }, suite.inherit_settings);
                                                    setHeaderKey('');
                                                    setHeaderVal('');
                                                }
                                            }}>Add</Button>
                                        </div>
                                    </div>
                                </div>

                                <div className="space-y-4">
                                    <h3 className="font-semibold flex items-center gap-2">
                                        <Edit className="h-4 w-4 text-primary" />
                                        Query Parameters
                                    </h3>
                                    <div className="space-y-2">
                                        {/* Inherited Params (Read-only) */}
                                        {suite.inherit_settings && suite.effective_settings?.params &&
                                            Object.entries(suite.effective_settings.params)
                                                .filter(([key]) => !suite.settings?.params?.[key]) // Only show if not overridden
                                                .map(([key, value]: [string, any], idx) => (
                                                    <div key={`inherited-param-${idx}`} className="flex gap-2 opacity-60">
                                                        <input disabled value={key} className="flex-1 px-3 py-1 bg-gray-100 border rounded text-sm italic" />
                                                        <input disabled value={value} className="flex-1 px-3 py-1 bg-gray-100 border rounded text-sm italic" />
                                                        <div className="w-8" /> {/* Spacer for trash icon */}
                                                    </div>
                                                ))}

                                        {/* Custom Params */}
                                        {Object.entries(suite.settings?.params || {}).map(([key, value]: [string, any], idx) => (
                                            <div key={idx} className="flex gap-2">
                                                <input disabled value={key} className="flex-1 px-3 py-1 bg-white border rounded text-sm font-medium" />
                                                <input disabled value={value} className="flex-1 px-3 py-1 bg-white border rounded text-sm" />
                                                <Button variant="ghost" size="sm" onClick={() => {
                                                    const newParams = { ...suite.settings.params };
                                                    delete newParams[key];
                                                    handleUpdateSettings({ ...suite.settings, params: newParams }, suite.inherit_settings);
                                                }}>
                                                    <Trash2 className="h-4 w-4 text-red-500" />
                                                </Button>
                                            </div>
                                        ))}
                                        <div className="flex gap-2 pt-2">
                                            <input
                                                value={paramKey}
                                                onChange={(e) => setParamKey(e.target.value)}
                                                placeholder="Key"
                                                className="flex-1 px-3 py-1 border rounded text-sm"
                                            />
                                            <input
                                                value={paramVal}
                                                onChange={(e) => setParamVal(e.target.value)}
                                                placeholder="Value"
                                                className="flex-1 px-3 py-1 border rounded text-sm"
                                            />
                                            <Button size="sm" onClick={() => {
                                                if (paramKey && paramVal) {
                                                    const currentSettings = suite.settings || { headers: {}, params: {} };
                                                    handleUpdateSettings({
                                                        ...currentSettings,
                                                        params: { ...(currentSettings.params || {}), [paramKey]: paramVal }
                                                    }, suite.inherit_settings);
                                                    setParamKey('');
                                                    setParamVal('');
                                                }
                                            }}>Add</Button>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Allowed Domains */}
                            <div className="space-y-4 col-span-full border-t pt-6">
                                <h3 className="font-semibold flex items-center gap-2">
                                    <Edit className="h-4 w-4 text-primary" />
                                    Allowed Domains (Allowlist)
                                </h3>
                                <p className="text-sm text-gray-500">
                                    By default, custom headers are only sent to the source domain of the test URL.
                                    Add domains here to allow headers to be sent to them as well.
                                </p>
                                <div className="space-y-2">
                                    {/* Inherited Allowed Domains */}
                                    {suite.inherit_settings && suite.effective_settings?.allowed_domains &&
                                        suite.effective_settings.allowed_domains
                                            .filter((d: any) => {
                                                const domainName = typeof d === 'string' ? d : d.domain;
                                                const currentDomains = suite.settings?.allowed_domains || [];
                                                return !currentDomains.some((cd: any) => (typeof cd === 'string' ? cd : cd.domain) === domainName);
                                            })
                                            .map((d: any, idx: number) => {
                                                const domainName = typeof d === 'string' ? d : d.domain;
                                                const allowHeaders = typeof d === 'string' ? true : d.headers !== false;
                                                const allowParams = typeof d === 'string' ? false : d.params === true;
                                                return (
                                                    <div key={`inherited-domain-${idx}`} className="flex items-center gap-2 opacity-60">
                                                        <input disabled value={domainName} className="flex-1 px-3 py-1 bg-gray-100 border rounded text-sm italic" />
                                                        <div className="flex items-center gap-2 text-xs text-gray-500">
                                                            <label className="flex items-center gap-1 cursor-not-allowed">
                                                                <input type="checkbox" checked={allowHeaders} disabled /> Headers
                                                            </label>
                                                            <label className="flex items-center gap-1 cursor-not-allowed">
                                                                <input type="checkbox" checked={allowParams} disabled /> Params
                                                            </label>
                                                        </div>
                                                        <div className="w-8" />
                                                    </div>
                                                );
                                            })}

                                    {/* Custom Allowed Domains */}
                                    {(suite.settings?.allowed_domains || []).map((d: any, idx: number) => {
                                        const domainName = typeof d === 'string' ? d : d.domain;
                                        const allowHeaders = typeof d === 'string' ? true : d.headers !== false;
                                        const allowParams = typeof d === 'string' ? false : d.params === true;

                                        return (
                                            <div key={idx} className="flex items-center gap-2">
                                                <input disabled value={domainName} className="flex-1 px-3 py-1 bg-white border rounded text-sm" />
                                                <div className="flex items-center gap-2 text-xs">
                                                    <label className="flex items-center gap-1 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={allowHeaders}
                                                            onChange={(e) => {
                                                                const newDomains = [...suite.settings.allowed_domains];
                                                                newDomains[idx] = {
                                                                    domain: domainName,
                                                                    headers: e.target.checked,
                                                                    params: allowParams
                                                                };
                                                                handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings);
                                                            }}
                                                        /> Headers
                                                    </label>
                                                    <label className="flex items-center gap-1 cursor-pointer">
                                                        <input
                                                            type="checkbox"
                                                            checked={allowParams}
                                                            onChange={(e) => {
                                                                const newDomains = [...suite.settings.allowed_domains];
                                                                newDomains[idx] = {
                                                                    domain: domainName,
                                                                    headers: allowHeaders,
                                                                    params: e.target.checked
                                                                };
                                                                handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings);
                                                            }}
                                                        /> Params
                                                    </label>
                                                </div>
                                                <Button variant="ghost" size="sm" onClick={() => {
                                                    const newDomains = suite.settings.allowed_domains.filter((_: any, i: number) => i !== idx);
                                                    handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings);
                                                }}>
                                                    <Trash2 className="h-4 w-4 text-red-500" />
                                                </Button>
                                            </div>
                                        );
                                    })}
                                    <div className="flex gap-2 pt-2">
                                        <input
                                            id="new-domain-input"
                                            placeholder="e.g., api.example.com"
                                            className="flex-1 px-3 py-1 border rounded text-sm"
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter') {
                                                    const val = (e.target as HTMLInputElement).value;
                                                    if (val) {
                                                        const currentSettings = suite.settings || {};
                                                        const currentDomains = currentSettings.allowed_domains || [];
                                                        // Check if domain already exists (handling both string and object)
                                                        const exists = currentDomains.some((d: any) => (typeof d === 'string' ? d : d.domain) === val);

                                                        if (!exists) {
                                                            handleUpdateSettings({
                                                                ...currentSettings,
                                                                allowed_domains: [...currentDomains, { domain: val, headers: true, params: false }]
                                                            }, suite.inherit_settings);
                                                            (e.target as HTMLInputElement).value = '';
                                                        }
                                                    }
                                                }
                                            }}
                                        />
                                        <Button size="sm" onClick={() => {
                                            const input = document.getElementById('new-domain-input') as HTMLInputElement;
                                            const val = input.value;
                                            if (val) {
                                                const currentSettings = suite.settings || {};
                                                const currentDomains = currentSettings.allowed_domains || [];
                                                const exists = currentDomains.some((d: any) => (typeof d === 'string' ? d : d.domain) === val);

                                                if (!exists) {
                                                    handleUpdateSettings({
                                                        ...currentSettings,
                                                        allowed_domains: [...currentDomains, { domain: val, headers: true, params: false }]
                                                    }, suite.inherit_settings);
                                                    input.value = '';
                                                }
                                            }
                                        }}>Add Domain</Button>
                                    </div>
                                </div>
                            </div>

                            {/* Domain Specific Settings */}
                            <div className="space-y-4 col-span-full border-t pt-6">
                                <h3 className="font-semibold flex items-center gap-2">
                                    <Edit className="h-4 w-4 text-primary" />
                                    Domain-Specific Settings
                                </h3>
                                <p className="text-sm text-gray-500">
                                    Define specific headers for other domains. These override the default headers.
                                </p>

                                {/* List existing domain settings */}
                                {Object.entries(suite.settings?.domain_settings || {}).map(([domain, settings]: [string, any], idx) => (
                                    <div key={idx} className="border rounded-lg p-4 space-y-3">
                                        <div className="flex justify-between items-center">
                                            <h4 className="font-medium">{domain}</h4>
                                            <Button variant="ghost" size="sm" onClick={() => {
                                                const newDomainSettings = { ...suite.settings.domain_settings };
                                                delete newDomainSettings[domain];
                                                handleUpdateSettings({ ...suite.settings, domain_settings: newDomainSettings }, suite.inherit_settings);
                                            }}>
                                                <Trash2 className="h-4 w-4 text-red-500" />
                                            </Button>
                                        </div>
                                        {/* Headers for this domain */}
                                        <div className="pl-4 border-l-2 border-gray-100 space-y-2">
                                            <h5 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Headers</h5>
                                            {Object.entries(settings.headers || {}).map(([hKey, hVal]: [string, any], hIdx) => (
                                                <div key={hIdx} className="flex gap-2 text-sm">
                                                    <span className="font-mono text-gray-600">{hKey}:</span>
                                                    <span className="text-gray-900">{hVal}</span>
                                                    <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={() => {
                                                        const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                        const newHeaders = { ...currentDomainSettings.headers };
                                                        delete newHeaders[hKey];

                                                        handleUpdateSettings({
                                                            ...suite.settings,
                                                            domain_settings: {
                                                                ...suite.settings.domain_settings,
                                                                [domain]: { ...currentDomainSettings, headers: newHeaders }
                                                            }
                                                        }, suite.inherit_settings);
                                                    }}>
                                                        <Trash2 className="h-3 w-3 text-red-500" />
                                                    </Button>
                                                </div>
                                            ))}
                                            <div className="flex gap-2 pt-1">
                                                <input placeholder="Key" className="flex-1 px-2 py-1 text-sm border rounded" id={`header-key-${idx}`} />
                                                <input placeholder="Value" className="flex-1 px-2 py-1 text-sm border rounded" id={`header-val-${idx}`} />
                                                <Button size="sm" variant="outline" onClick={() => {
                                                    const keyInput = document.getElementById(`header-key-${idx}`) as HTMLInputElement;
                                                    const valInput = document.getElementById(`header-val-${idx}`) as HTMLInputElement;
                                                    if (keyInput.value && valInput.value) {
                                                        const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                        const newHeaders = { ...currentDomainSettings.headers, [keyInput.value]: valInput.value };

                                                        handleUpdateSettings({
                                                            ...suite.settings,
                                                            domain_settings: {
                                                                ...suite.settings.domain_settings,
                                                                [domain]: { ...currentDomainSettings, headers: newHeaders }
                                                            }
                                                        }, suite.inherit_settings);
                                                        keyInput.value = '';
                                                        valInput.value = '';
                                                    }
                                                }}>Add Header</Button>
                                            </div>
                                        </div>

                                        {/* Params for this domain */}
                                        <div className="pl-4 border-l-2 border-gray-100 space-y-2 mt-4">
                                            <h5 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Query Parameters</h5>
                                            {Object.entries(settings.params || {}).map(([pKey, pVal]: [string, any], pIdx) => (
                                                <div key={pIdx} className="flex gap-2 text-sm">
                                                    <span className="font-mono text-gray-600">{pKey}:</span>
                                                    <span className="text-gray-900">{pVal}</span>
                                                    <Button variant="ghost" size="sm" className="h-5 w-5 p-0" onClick={() => {
                                                        const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                        const newParams = { ...currentDomainSettings.params };
                                                        delete newParams[pKey];

                                                        handleUpdateSettings({
                                                            ...suite.settings,
                                                            domain_settings: {
                                                                ...suite.settings.domain_settings,
                                                                [domain]: { ...currentDomainSettings, params: newParams }
                                                            }
                                                        }, suite.inherit_settings);
                                                    }}>
                                                        <Trash2 className="h-3 w-3 text-red-500" />
                                                    </Button>
                                                </div>
                                            ))}
                                            <div className="flex gap-2 pt-1">
                                                <input placeholder="Key" className="flex-1 px-2 py-1 text-sm border rounded" id={`param-key-${idx}`} />
                                                <input placeholder="Value" className="flex-1 px-2 py-1 text-sm border rounded" id={`param-val-${idx}`} />
                                                <Button size="sm" variant="outline" onClick={() => {
                                                    const keyInput = document.getElementById(`param-key-${idx}`) as HTMLInputElement;
                                                    const valInput = document.getElementById(`param-val-${idx}`) as HTMLInputElement;
                                                    if (keyInput.value && valInput.value) {
                                                        const currentDomainSettings = suite.settings.domain_settings[domain] || { headers: {}, params: {} };
                                                        const newParams = { ...currentDomainSettings.params, [keyInput.value]: valInput.value };

                                                        handleUpdateSettings({
                                                            ...suite.settings,
                                                            domain_settings: {
                                                                ...suite.settings.domain_settings,
                                                                [domain]: { ...currentDomainSettings, params: newParams }
                                                            }
                                                        }, suite.inherit_settings);
                                                        keyInput.value = '';
                                                        valInput.value = '';
                                                    }
                                                }}>Add Param</Button>
                                            </div>
                                        </div>
                                    </div>
                                ))}

                                {/* Add new domain setting */}
                                <div className="flex gap-2 pt-2">
                                    <input
                                        id="new-domain-setting-input"
                                        placeholder="New Domain (e.g., analytics.example.com)"
                                        className="flex-1 px-3 py-1 border rounded text-sm"
                                    />
                                    <Button size="sm" onClick={() => {
                                        const input = document.getElementById('new-domain-setting-input') as HTMLInputElement;
                                        const val = input.value;
                                        if (val) {
                                            const currentSettings = suite.settings || {};
                                            const currentDomainSettings = currentSettings.domain_settings || {};
                                            if (!currentDomainSettings[val]) {
                                                handleUpdateSettings({
                                                    ...currentSettings,
                                                    domain_settings: {
                                                        ...currentDomainSettings,
                                                        [val]: { headers: {} }
                                                    }
                                                }, suite.inherit_settings);
                                                input.value = '';
                                            }
                                        }
                                    }}>Add Domain Setting</Button>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
