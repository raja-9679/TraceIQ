import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { api, getSettings, updateTestSuite } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select"
import { usePermission } from "@/hooks/usePermission";
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, Play, Trash2, Edit, FileText, FolderOpen, Search, Loader2, ChevronDown, AlertCircle, Download, Upload } from 'lucide-react';
import {
    DropdownMenu,
    DropdownMenuCheckboxItem,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from 'sonner';
import { useState, useEffect } from 'react';
import { triggerRun, exportTestCase, importTestCase, exportTestSuite, importTestSuite, getAuditLog } from '@/lib/api';
import { History } from 'lucide-react';
import { ChevronRight } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const containerVariants = {
    hidden: { opacity: 0 },
    show: {
        opacity: 1,
        transition: {
            staggerChildren: 0.1
        }
    }
};

const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0 }
};

const tabVariants = {
    hidden: { opacity: 0, y: 10 },
    visible: { opacity: 1, y: 0 },
    exit: { opacity: 0, y: -10 }
};

export default function SuiteDetails() {
    const { suiteId } = useParams();
    const navigate = useNavigate();
    const queryClient = useQueryClient();

    // We need project ID for permissions. Assuming suiteId implies project context.
    const { data: suiteDataForPerms } = useQuery({
        queryKey: ['suite-perms', suiteId],
        queryFn: () => api.get(`/suites/${suiteId}`).then(res => res.data),
        enabled: !!suiteId,
    });
    const { can } = usePermission();
    const projectId = suiteDataForPerms?.project_id;

    const [showSubModuleDialog, setShowSubModuleDialog] = useState(false);
    const [newModuleName, setNewModuleName] = useState('');
    const [newModuleDesc, setNewModuleDesc] = useState('');
    const [showRenameDialog, setShowRenameDialog] = useState(false);
    const [renameName, setRenameName] = useState('');
    const [renameDesc, setRenameDesc] = useState('');
    const [activeTab, setActiveTab] = useState<'tests' | 'settings' | 'audit'>('tests');
    const [showDeleteDialog, setShowDeleteDialog] = useState(false);
    const [deleteConfirmName, setDeleteConfirmName] = useState('');
    const [showDeleteTestCaseDialog, setShowDeleteTestCaseDialog] = useState(false);
    const [testCaseToDelete, setTestCaseToDelete] = useState<{ id: number; name: string } | null>(null);
    const [headerKey, setHeaderKey] = useState('');
    const [headerVal, setHeaderVal] = useState('');
    const [paramKey, setParamKey] = useState('');
    const [paramVal, setParamVal] = useState('');
    const [moduleError, setModuleError] = useState<string | null>(null);
    const [successMessage, setSuccessMessage] = useState<string | null>(null);
    const [searchTerm, setSearchTerm] = useState('');
    const [selectedBrowsers, setSelectedBrowsers] = useState<string[]>(['chromium']);
    const [selectedDevices, setSelectedDevices] = useState<string[]>(['Desktop']);
    const location = useLocation();

    // Load user settings to check if multi-browser is enabled
    const { data: userSettings } = useQuery({
        queryKey: ['settings'],
        queryFn: getSettings,
    });

    // Initialize selections from settings when loaded
    useEffect(() => {
        if (userSettings) {
            if (userSettings.multi_browser_enabled) {
                setSelectedBrowsers(userSettings.selected_browsers);
            } else {
                setSelectedBrowsers([userSettings.default_browser]);
            }

            if (userSettings.multi_device_enabled) {
                setSelectedDevices(userSettings.selected_devices);
            } else {
                setSelectedDevices([userSettings.default_device]);
            }
        }
    }, [userSettings]);

    const { data: auditLogs } = useQuery({
        queryKey: ['audit', suiteId],
        queryFn: () => getAuditLog('suite', Number(suiteId)),
        enabled: !!suiteId && activeTab === 'audit'
    });

    // Reset state when suite changes
    useEffect(() => {
        setSearchTerm('');
        setActiveTab('tests');
    }, [suiteId]);

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



    const createSubModule = useMutation({
        mutationFn: (data: { name: string; description?: string; parent_id: number }) =>
            api.post(`/suites`, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setNewModuleName('');
            setNewModuleDesc('');
            setShowSubModuleDialog(false);
            setModuleError(null);
            toast.success('Sub-module created successfully');
        },
        onError: (error: any) => {
            const msg = error.response?.data?.detail || "Failed to create sub-module";
            setModuleError(msg);
            toast.error(msg);
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
            toast.success('Module deleted successfully');
        },
        onError: (_error: any) => {
            toast.error('Failed to delete module');
        }
    });

    const deleteTestCase = useMutation({
        mutationFn: (id: number) => api.delete(`/cases/${id}`),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            setShowDeleteTestCaseDialog(false);
            setTestCaseToDelete(null);
            toast.success('Test case deleted successfully');
        },
        onError: (_error: any) => {
            toast.error('Failed to delete test case');
        }
    });

    const updateSettings = useMutation({
        mutationFn: (data: any) => api.put(`/suites/${suiteId}`, data),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
            if (variables.successMessage) {
                toast.success(variables.successMessage);
            } else {
                toast.success('Settings updated successfully');
            }
        },
        onError: (_error: any) => {
            toast.error('Failed to update settings');
        }
    });

    const renameSuite = useMutation({
        mutationFn: (data: { name: string; description?: string }) =>
            updateTestSuite(Number(suiteId), data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['suite'] });
            setShowRenameDialog(false);
            toast.success('Module updated successfully');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to update module');
        }
    });

    const handleRenameSuite = () => {
        if (renameName.trim()) {
            renameSuite.mutate({
                name: renameName,
                description: renameDesc || undefined,
            });
        }
    };

    const handleDeleteSuite = () => {
        if (deleteConfirmName === suite.name) {
            deleteSuite.mutate();
        }
    };

    const handleUpdateSettings = (newSettings: any, inherit: boolean, successMessage?: string) => {
        const settings = newSettings || { headers: {}, params: {} };
        updateSettings.mutate({
            settings: settings,
            inherit_settings: inherit,
            successMessage
        });
    };

    const [isRunning, setIsRunning] = useState(false);

    const handleRunSuite = async () => {
        // Validate selections
        if (selectedBrowsers.length === 0) {
            setModuleError('Please select at least one browser');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }
        if (selectedDevices.length === 0) {
            setModuleError('Please select at least one device');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }

        try {
            setIsRunning(true);
            await triggerRun(Number(suiteId), undefined, selectedBrowsers, selectedDevices);
            toast.success('Test suite run started');
            navigate('/runs');
        } catch (error: any) {
            console.error("Failed to start run:", error);
            toast.error(error?.response?.data?.detail || 'Failed to start test run');
        } finally {
            setIsRunning(false);
        }
    };

    const handleRunTestCase = async (caseId: number) => {
        // Validate selections
        if (selectedBrowsers.length === 0) {
            setModuleError('Please select at least one browser');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }
        if (selectedDevices.length === 0) {
            setModuleError('Please select at least one device');
            setTimeout(() => setModuleError(null), 3000);
            return;
        }

        try {
            await triggerRun(Number(suiteId), caseId, selectedBrowsers, selectedDevices);
            toast.success('Test case run started');
            navigate('/runs');
        } catch (error: any) {
            console.error("Failed to start run:", error);
            toast.error(error?.response?.data?.detail || 'Failed to start test run');
        }
    };

    const handleExportCase = async (caseId: number, caseName: string) => {
        try {
            const data = await exportTestCase(caseId);
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${caseName.replace(/\s+/g, '_')}_test_case.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Test case exported successfully');
        } catch (error) {
            toast.error('Failed to export test case');
        }
    };

    const handleImportCase = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = async (e) => {
            try {
                const content = e.target?.result as string;
                const data = JSON.parse(content);
                await importTestCase(Number(suiteId), data);
                queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
                toast.success('Test case imported successfully');
            } catch (error) {
                toast.error('Failed to import test case');
            }
        };
        reader.readAsText(file);
        // Reset input
        event.target.value = '';
    };

    const handleExportSuite = async () => {
        try {
            const data = await exportTestSuite(Number(suiteId));
            const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${suite.name.replace(/\s+/g, '_')}_suite.json`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Module exported successfully');
        } catch (error) {
            toast.error('Failed to export module');
        }
    };

    const handleImportSuite = async (event: React.ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = async (e) => {
            try {
                const content = e.target?.result as string;
                const data = JSON.parse(content);
                await importTestSuite(Number(suiteId), data);
                queryClient.invalidateQueries({ queryKey: ['suite', suiteId] });
                toast.success('Module imported successfully');
            } catch (error) {
                toast.error('Failed to import module');
            }
        };
        reader.readAsText(file);
        // Reset input
        event.target.value = '';
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
            <div className="flex flex-col space-y-4 md:flex-row md:items-center md:justify-between md:space-y-0">
                <div className="flex flex-col space-y-2">
                    {/* Breadcrumbs */}
                    <div className="flex items-center text-sm text-muted-foreground">
                        <Button variant="link" size="sm" className="p-0 h-auto text-muted-foreground hover:text-primary font-medium" onClick={() => navigate('/suites')}>
                            Suites
                        </Button>
                        {suite.parent && (
                            <>
                                <ChevronRight className="h-3 w-3 mx-2 text-muted-foreground/50" />
                                <Button variant="link" size="sm" className="p-0 h-auto text-muted-foreground hover:text-primary font-medium" onClick={() => navigate(`/suites/${suite.parent.id}`)}>
                                    {suite.parent.name}
                                </Button>
                            </>
                        )}
                        <ChevronRight className="h-3 w-3 mx-2 text-muted-foreground/50" />
                        <span className="font-semibold text-foreground">{suite.name}</span>
                    </div>

                    {/* Title & Badges */}
                    <div className="flex items-center gap-3">
                        <h1 className="text-3xl font-bold tracking-tight text-foreground">{suite.name}</h1>
                        <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => {
                            setRenameName(suite.name);
                            setRenameDesc(suite.description || '');
                            setShowRenameDialog(true);
                        }}>
                            <Edit className="h-4 w-4 text-muted-foreground hover:text-primary" />
                        </Button>
                        <span className={`px-2.5 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-wider ${suite.execution_mode === 'separate'
                            ? 'bg-purple-500/10 text-purple-600 border border-purple-200'
                            : 'bg-blue-500/10 text-blue-600 border border-blue-200'
                            }`}>
                            {suite.execution_mode}
                        </span>
                    </div>

                    {/* Description */}
                    {suite.description && (
                        <p className="text-muted-foreground max-w-2xl">{suite.description}</p>
                    )}

                    {/* Meta Info */}
                    <div className="flex items-center gap-6 pt-1 text-sm text-muted-foreground">
                        <div className="flex items-center gap-2">
                            <div className="p-1 rounded-md bg-secondary">
                                <FileText className="h-3.5 w-3.5" />
                            </div>
                            <span className="font-medium">{suite.total_test_cases || 0} test cases</span>
                        </div>
                        {suite.total_sub_modules > 0 && (
                            <div className="flex items-center gap-2">
                                <div className="p-1 rounded-md bg-secondary">
                                    <FolderOpen className="h-3.5 w-3.5" />
                                </div>
                                <span className="font-medium">{suite.total_sub_modules} modules</span>
                            </div>
                        )}
                    </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-3">
                    <Button
                        variant="outline"
                        onClick={handleExportSuite}
                        className="text-primary hover:bg-primary/10 border-primary/20"
                    >
                        <Download className="mr-2 h-4 w-4" /> Export Module
                    </Button>
                    {can("project:delete", { projectId }) && (
                        <Button
                            variant="outline"
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive border-destructive/20"
                            onClick={() => setShowDeleteDialog(true)}
                        >
                            <Trash2 className="mr-2 h-4 w-4" /> Delete
                        </Button>
                    )}
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
                                    className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:ring-2 focus:ring-destructive outline-none"
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
                                    {deleteSuite.isPending ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Deleting...
                                        </>
                                    ) : (
                                        'Delete Everything'
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Delete Test Case Confirmation Modal */}
            {showDeleteTestCaseDialog && testCaseToDelete && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                    <Card className="w-full max-w-md mx-4">
                        <CardHeader>
                            <CardTitle className="text-red-600">Delete Test Case?</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-gray-600">
                                Are you sure you want to delete <strong>{testCaseToDelete.name}</strong>? This action cannot be undone.
                            </p>
                            <div className="flex gap-2 justify-end">
                                <Button variant="outline" onClick={() => { setShowDeleteTestCaseDialog(false); setTestCaseToDelete(null); }}>Cancel</Button>
                                <Button
                                    variant="destructive"
                                    disabled={deleteTestCase.isPending}
                                    onClick={() => deleteTestCase.mutate(testCaseToDelete.id)}
                                >
                                    {deleteTestCase.isPending ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Deleting...
                                        </>
                                    ) : (
                                        'Delete'
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}

            {/* Tabs */}
            <div className="flex border-b border-border">
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
                <button
                    className={`px-6 py-3 text-sm font-medium border-b-2 transition-colors ${activeTab === 'audit' ? 'border-primary text-primary' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
                    onClick={() => setActiveTab('audit')}
                >
                    Audit Log
                </button>
            </div>

            <AnimatePresence mode="wait">
                {activeTab === 'tests' ? (
                    <motion.div
                        key="tests"
                        variants={tabVariants}
                        initial="hidden"
                        animate="visible"
                        exit="exit"
                        className="space-y-6"
                    >
                        <div className="flex justify-between items-center gap-4">
                            <div className="relative flex-1 max-w-sm">
                                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
                                <input
                                    type="text"
                                    placeholder="Search modules or test cases..."
                                    value={searchTerm}
                                    onChange={(e) => setSearchTerm(e.target.value)}
                                    className="w-full pl-10 pr-4 py-2 border border-input bg-background rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                />
                            </div>
                            <div className="flex items-center gap-2">
                                {suite.total_sub_modules === 0 && (
                                    <div className="flex items-center gap-2">
                                        {can("test:create", { projectId }) && (
                                            <Button onClick={() => navigate(`/suites/${suiteId}/builder`)}>
                                                <Plus className="mr-2 h-4 w-4" /> New Test Case
                                            </Button>
                                        )}
                                        {can("test:create", { projectId }) && (
                                            <div className="relative">
                                                <input
                                                    type="file"
                                                    accept=".json"
                                                    onChange={handleImportCase}
                                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                                    title="Import Test Case"
                                                />
                                                <Button variant="outline">
                                                    <Upload className="mr-2 h-4 w-4" /> Import Case
                                                </Button>
                                            </div>
                                        )}
                                    </div >
                                )}
                                {
                                    suite.total_test_cases === 0 && (
                                        <div className="flex items-center gap-2">
                                            {can("project:manage", { projectId }) && ( // Creating sub-module is structure update
                                                <Button variant="outline" onClick={() => setShowSubModuleDialog(true)}>
                                                    <FolderOpen className="mr-2 h-4 w-4" /> New Sub-Module
                                                </Button>
                                            )}
                                            {can("project:manage", { projectId }) && (
                                                <div className="relative">
                                                    <input
                                                        type="file"
                                                        accept=".json"
                                                        onChange={handleImportSuite}
                                                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                                        title="Import Module"
                                                    />
                                                    <Button variant="outline">
                                                        <Upload className="mr-2 h-4 w-4" /> Import Module
                                                    </Button>
                                                </div>
                                            )}
                                        </div>
                                    )
                                }

                                {/* Browser Selector - Only show if multi-browser is ENABLED */}
                                {
                                    userSettings && userSettings.multi_browser_enabled && (
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="outline" className="w-[140px] justify-between">
                                                    {selectedBrowsers.length > 0
                                                        ? (selectedBrowsers.length === 1 ? selectedBrowsers[0] : `${selectedBrowsers.length} Browsers`)
                                                        : "Select Browser"}
                                                    <ChevronDown className="ml-2 h-4 w-4 opacity-50" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent className="w-56">
                                                <DropdownMenuLabel>Browsers</DropdownMenuLabel>
                                                <DropdownMenuSeparator />
                                                {['chromium', 'firefox', 'webkit']
                                                    .filter(b => userSettings.selected_browsers.includes(b))
                                                    .map((browser) => (
                                                        <DropdownMenuCheckboxItem
                                                            key={browser}
                                                            checked={selectedBrowsers.includes(browser)}
                                                            onSelect={(e) => e.preventDefault()}
                                                            onCheckedChange={(checked) => {
                                                                if (checked) {
                                                                    setSelectedBrowsers([...selectedBrowsers, browser]);
                                                                } else {
                                                                    setSelectedBrowsers(selectedBrowsers.filter((b) => b !== browser));
                                                                }
                                                            }}
                                                        >
                                                            {browser.charAt(0).toUpperCase() + browser.slice(1)}
                                                        </DropdownMenuCheckboxItem>
                                                    ))}
                                            </DropdownMenuContent>
                                        </DropdownMenu>
                                    )
                                }

                                {/* Device Selector - Only show if multi-device is ENABLED */}
                                {
                                    userSettings && userSettings.multi_device_enabled && (
                                        <DropdownMenu>
                                            <DropdownMenuTrigger asChild>
                                                <Button variant="outline" className="w-[140px] justify-between">
                                                    {selectedDevices.length > 0
                                                        ? (selectedDevices.length === 1 ? selectedDevices[0] : `${selectedDevices.length} Devices`)
                                                        : "Select Device"}
                                                    <ChevronDown className="ml-2 h-4 w-4 opacity-50" />
                                                </Button>
                                            </DropdownMenuTrigger>
                                            <DropdownMenuContent className="w-56">
                                                <DropdownMenuLabel>Devices</DropdownMenuLabel>
                                                <DropdownMenuSeparator />
                                                {['Desktop', 'Mobile (Generic)', 'iPhone 13', 'Pixel 5']
                                                    .filter(d => userSettings.selected_devices.includes(d))
                                                    .map((device) => (
                                                        <DropdownMenuCheckboxItem
                                                            key={device}
                                                            checked={selectedDevices.includes(device)}
                                                            onSelect={(e) => e.preventDefault()}
                                                            onCheckedChange={(checked) => {
                                                                if (checked) {
                                                                    setSelectedDevices([...selectedDevices, device]);
                                                                } else {
                                                                    setSelectedDevices(selectedDevices.filter((d) => d !== device));
                                                                }
                                                            }}
                                                        >
                                                            {device}
                                                        </DropdownMenuCheckboxItem>
                                                    ))}
                                            </DropdownMenuContent>
                                        </DropdownMenu>
                                    )
                                }

                                <Button onClick={handleRunSuite} disabled={isRunning}>
                                    {isRunning ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            Starting...
                                        </>
                                    ) : (
                                        <>
                                            <Play className="mr-2 h-4 w-4" /> Run Suite
                                        </>
                                    )}
                                </Button>
                            </div >
                        </div >
                        {/* ... rest of the tests content ... */}



                        {/* Sub-Modules List */}
                        {
                            suite.sub_modules && suite.sub_modules.length > 0 && (
                                <div className="space-y-4">
                                    <div className="flex items-center justify-between">
                                        <h2 className="text-lg font-semibold tracking-tight flex items-center gap-2">
                                            <FolderOpen className="h-5 w-5 text-primary" />
                                            Sub-Modules ({suite.sub_modules.length})
                                        </h2>
                                        <Button variant="outline" size="sm" onClick={() => setShowSubModuleDialog(true)} className="h-8">
                                            <Plus className="mr-2 h-3.5 w-3.5" /> Add Module
                                        </Button>
                                    </div>
                                    <motion.div
                                        key={suiteId}
                                        className="grid gap-4 md:grid-cols-2 lg:grid-cols-3"
                                        variants={containerVariants}
                                        initial="hidden"
                                        animate="show"
                                    >
                                        <AnimatePresence mode='popLayout'>
                                            {suite.sub_modules
                                                .filter((sub: any) =>
                                                    sub.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                                                    (sub.description && sub.description.toLowerCase().includes(searchTerm.toLowerCase()))
                                                )
                                                .map((sub: any) => (
                                                    <motion.div
                                                        key={sub.id}
                                                        layout
                                                        variants={itemVariants}
                                                        initial="hidden"
                                                        animate="show"
                                                        exit={{ opacity: 0, scale: 0.9 }}
                                                        className="h-full"
                                                    >
                                                        <Card
                                                            className="h-full group hover:border-primary/50 transition-all cursor-pointer hover:shadow-md bg-card border-border"
                                                            onClick={() => navigate(`/suites/${sub.id}`)}
                                                        >
                                                            <CardContent className="p-5 flex items-start justify-between">
                                                                <div className="flex items-start space-x-4">
                                                                    <div className="p-2.5 bg-primary/10 rounded-xl group-hover:bg-primary/20 transition-colors mt-0.5">
                                                                        <FolderOpen className="h-5 w-5 text-primary" />
                                                                    </div>
                                                                    <div className="space-y-1">
                                                                        <div className="flex items-center gap-2">
                                                                            <span className="font-semibold text-foreground block tracking-tight">{sub.name}</span>
                                                                            <span className={`px-2 py-0.5 rounded-full text-[10px] uppercase font-bold tracking-wider ${sub.execution_mode === 'separate'
                                                                                ? 'bg-purple-500/10 text-purple-600 border border-purple-200'
                                                                                : 'bg-blue-500/10 text-blue-600 border border-blue-200'
                                                                                }`}>
                                                                                {sub.execution_mode}
                                                                            </span>
                                                                        </div>
                                                                        {sub.description && (
                                                                            <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                                                                                {sub.description}
                                                                            </p>
                                                                        )}
                                                                        <div className="flex items-center gap-3 pt-2 text-xs text-muted-foreground">
                                                                            <span>{sub.total_test_cases || 0} tests</span>
                                                                            <span className="w-1 h-1 rounded-full bg-border" />
                                                                            <span>{sub.total_sub_modules || 0} modules</span>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                                <ChevronRight className="h-4 w-4 text-muted-foreground/50 group-hover:text-primary transition-colors mt-2" />
                                                            </CardContent>
                                                        </Card>
                                                    </motion.div>
                                                ))}
                                        </AnimatePresence>
                                    </motion.div>
                                </div>
                            )
                        }

                        {/* Create Sub-Module Dialog */}
                        {
                            showSubModuleDialog && (
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
                                                className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
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
                                                className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                            />
                                        </div>
                                        <div className="flex gap-2">
                                            <Button onClick={handleCreateSubModule} disabled={!newModuleName.trim() || createSubModule.isPending}>
                                                {createSubModule.isPending ? (
                                                    <>
                                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                        Creating...
                                                    </>
                                                ) : (
                                                    'Create Module'
                                                )}
                                            </Button>
                                            <Button variant="outline" onClick={() => setShowSubModuleDialog(false)}>
                                                Cancel
                                            </Button>
                                        </div>
                                    </CardContent>
                                </Card>
                            )
                        }

                        {/* Rename Module Dialog */}
                        {
                            showRenameDialog && (
                                <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
                                    <Card className="w-full max-w-md mx-4">
                                        <CardHeader>
                                            <CardTitle>Edit Module</CardTitle>
                                        </CardHeader>
                                        <CardContent className="space-y-4">
                                            <div>
                                                <label className="block text-sm font-medium text-gray-700 mb-1">
                                                    Module Name *
                                                </label>
                                                <input
                                                    type="text"
                                                    value={renameName}
                                                    onChange={(e) => setRenameName(e.target.value)}
                                                    className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                                />
                                            </div>
                                            <div>
                                                <label className="block text-sm font-medium text-gray-700 mb-1">
                                                    Description
                                                </label>
                                                <textarea
                                                    value={renameDesc}
                                                    onChange={(e) => setRenameDesc(e.target.value)}
                                                    rows={3}
                                                    className="w-full px-3 py-2 border border-input bg-background rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                                />
                                            </div>
                                            <div className="flex gap-2 justify-end">
                                                <Button variant="outline" onClick={() => setShowRenameDialog(false)}>
                                                    Cancel
                                                </Button>
                                                <Button onClick={handleRenameSuite} disabled={!renameName.trim() || renameSuite.isPending}>
                                                    {renameSuite.isPending ? (
                                                        <>
                                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                            Saving...
                                                        </>
                                                    ) : (
                                                        'Save Changes'
                                                    )}
                                                </Button>
                                            </div>
                                        </CardContent>
                                    </Card>
                                </div>
                            )
                        }

                        {/* Test Cases List */}
                        {
                            suite.total_sub_modules === 0 && (
                                <Card className="border-border">
                                    <CardHeader className="border-b border-border bg-muted/30">
                                        <div className="flex items-center justify-between">
                                            <CardTitle className="text-lg font-semibold tracking-tight">Test Cases ({suite.test_cases?.length || 0})</CardTitle>
                                        </div>
                                    </CardHeader>
                                    <CardContent className="p-0">
                                        {suite.test_cases && suite.test_cases.length > 0 ? (
                                            <motion.div
                                                className="divide-y divide-border"
                                                variants={containerVariants}
                                                initial="hidden"
                                                animate="show"
                                            >
                                                <AnimatePresence mode='popLayout'>
                                                    {suite.test_cases
                                                        .filter((tc: any) => tc.name.toLowerCase().includes(searchTerm.toLowerCase()))
                                                        .map((testCase: any) => (
                                                            <motion.div
                                                                key={testCase.id}
                                                                layout
                                                                variants={itemVariants}
                                                                initial="hidden"
                                                                animate="show"
                                                                exit={{ opacity: 0, x: -20 }}
                                                                className="group p-4 hover:bg-muted/30 transition-colors"
                                                            >
                                                                <div className="flex items-start justify-between">
                                                                    <div className="flex-1 space-y-3">
                                                                        <div className="flex items-center space-x-3">
                                                                            <div className="p-2 rounded-md bg-primary/10 text-primary">
                                                                                <FileText className="h-4 w-4" />
                                                                            </div>
                                                                            <h3 className="font-semibold text-base text-foreground">{testCase.name}</h3>
                                                                        </div>
                                                                        {testCase.steps && testCase.steps.length > 0 && (
                                                                            <div className="ml-11">
                                                                                <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-2">Steps</p>
                                                                                <ol className="list-decimal list-inside space-y-1.5">
                                                                                    {testCase.steps.map((step: any, idx: number) => (
                                                                                        <li key={idx} className="text-sm text-muted-foreground pl-1">
                                                                                            {typeof step === 'string' ? (
                                                                                                step
                                                                                            ) : (
                                                                                                <span>
                                                                                                    <span className="font-medium text-primary">{step.type}</span>
                                                                                                    {step.selector && <span className="text-muted-foreground"> on <code className="bg-muted px-1 py-0.5 rounded text-xs">{step.selector}</code></span>}
                                                                                                    {step.value && <span className="text-foreground"> "{step.value}"</span>}
                                                                                                </span>
                                                                                            )}
                                                                                        </li>
                                                                                    ))}
                                                                                </ol>
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                                                        <Button variant="ghost" size="sm" onClick={() => handleRunTestCase(testCase.id)} className="h-8 w-8 p-0 hover:bg-green-50 hover:text-green-600">
                                                                            <Play className="h-4 w-4" />
                                                                        </Button>
                                                                        {can("test:create", { projectId }) && (
                                                                            <Button variant="ghost" size="sm" onClick={() => navigate(`/suites/${suiteId}/cases/${testCase.id}/edit`)} className="h-8 w-8 p-0">
                                                                                <Edit className="h-4 w-4" />
                                                                            </Button>
                                                                        )}
                                                                        <Button variant="ghost" size="sm" onClick={() => handleExportCase(testCase.id, testCase.name)} className="h-8 w-8 p-0 text-muted-foreground hover:text-primary">
                                                                            <Download className="h-4 w-4" />
                                                                        </Button>
                                                                        {can("test:create", { projectId }) && (
                                                                            <Button variant="ghost" size="sm" onClick={() => {
                                                                                setTestCaseToDelete({ id: testCase.id, name: testCase.name });
                                                                                setShowDeleteTestCaseDialog(true);
                                                                            }} className="h-8 w-8 p-0 text-muted-foreground hover:text-destructive hover:bg-destructive/10">
                                                                                <Trash2 className="h-4 w-4" />
                                                                            </Button>
                                                                        )}
                                                                    </div>
                                                                </div>
                                                            </motion.div>
                                                        ))}
                                                </AnimatePresence>
                                            </motion.div>
                                        ) : (
                                            <div className="text-center py-16">
                                                <div className="w-16 h-16 bg-muted rounded-full flex items-center justify-center mx-auto mb-4">
                                                    <FileText className="h-8 w-8 text-muted-foreground" />
                                                </div>
                                                <h3 className="text-lg font-semibold text-foreground mb-2">No test cases yet</h3>
                                                <p className="text-muted-foreground mb-6 max-w-sm mx-auto">Get started by adding your first test case to this suite. You can define steps and assertions.</p>
                                                {suite.total_sub_modules === 0 && (
                                                    <div className="flex items-center gap-2 justify-center">
                                                        {can("test:create", { projectId }) && (
                                                            <Button onClick={() => navigate(`/suites/${suiteId}/builder`)}>
                                                                <Plus className="mr-2 h-4 w-4" /> Add Test Case
                                                            </Button>
                                                        )}
                                                        {can("test:create", { projectId }) && (
                                                            <div className="relative">
                                                                <input
                                                                    type="file"
                                                                    accept=".json"
                                                                    onChange={handleImportCase}
                                                                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                                                                    title="Import Test Case"
                                                                />
                                                                <Button variant="outline">
                                                                    <Upload className="mr-2 h-4 w-4" /> Import Case
                                                                </Button>
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </CardContent>
                                </Card>
                            )
                        }
                    </motion.div >
                ) : activeTab === 'settings' ? (
                    <motion.div
                        key="settings"
                        variants={tabVariants}
                        initial="hidden"
                        animate="visible"
                        exit="exit"
                        className="space-y-6"
                    >
                        <Card>
                            <CardHeader>
                                <CardTitle>Module Settings</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-6">
                                {suite.parent_id && (
                                    <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border border-border">
                                        <div>
                                            <h3 className="font-semibold text-foreground">Inherit Settings from Parent</h3>
                                            <p className="text-sm text-muted-foreground">Automatically use headers and parameters defined in parent modules.</p>
                                        </div>
                                        <Button
                                            variant={suite.inherit_settings ? "default" : "outline"}
                                            onClick={() => handleUpdateSettings(suite.settings, !suite.inherit_settings, "Inheritance settings updated")}
                                        >
                                            {suite.inherit_settings ? "Inheritance On" : "Inheritance Off"}
                                        </Button>
                                    </div>
                                )}

                                {/* Execution Mode Setting */}
                                <div className="flex items-center justify-between p-4 bg-muted/30 rounded-lg border border-border">
                                    <div>
                                        <h3 className="font-semibold text-foreground">Execution Mode</h3>
                                        <p className="text-sm text-muted-foreground">
                                            {suite.execution_mode === 'continuous'
                                                ? "Running all test cases in a single browser session."
                                                : "Running each test case in a separate browser session."}
                                        </p>
                                        {suite.sub_modules && suite.sub_modules.length > 0 && (
                                            <p className="text-xs text-amber-600 mt-1 flex items-center gap-1">
                                                <AlertCircle className="h-3 w-3" />
                                                Locked to Separate mode because this module has sub-modules.
                                            </p>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Select
                                            value={suite.execution_mode}
                                            disabled={suite.sub_modules && suite.sub_modules.length > 0}
                                            onValueChange={(value) => updateSettings.mutate({
                                                execution_mode: value,
                                                successMessage: `Execution mode updated to ${value}`
                                            })}
                                        >
                                            <SelectTrigger className="w-[180px]">
                                                <SelectValue placeholder="Select mode" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="continuous">Continuous</SelectItem>
                                                <SelectItem value="separate">Separate</SelectItem>
                                            </SelectContent>
                                        </Select>
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
                                                            <input disabled value={key} className="flex-1 px-3 py-1 bg-muted border border-input rounded text-sm italic text-muted-foreground" />
                                                            <input disabled value={value} className="flex-1 px-3 py-1 bg-muted border border-input rounded text-sm italic text-muted-foreground" />
                                                            <div className="w-8" /> {/* Spacer for trash icon */}
                                                        </div>
                                                    ))}

                                            {/* Custom Headers */}
                                            {Object.entries(suite.settings?.headers || {}).map(([key, value]: [string, any], idx) => (
                                                <div key={idx} className="flex gap-2">
                                                    <input disabled value={key} className="flex-1 px-3 py-1 bg-background border border-input rounded text-sm font-medium" />
                                                    <input disabled value={value} className="flex-1 px-3 py-1 bg-background border border-input rounded text-sm" />
                                                    <Button variant="ghost" size="sm" onClick={() => {
                                                        const newHeaders = { ...suite.settings.headers };
                                                        delete newHeaders[key];
                                                        handleUpdateSettings({ ...suite.settings, headers: newHeaders }, suite.inherit_settings, `Header '${key}' removed`);
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
                                                    className="flex-1 px-3 py-1 border border-input bg-background rounded text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                                                />
                                                <input
                                                    value={headerVal}
                                                    onChange={(e) => setHeaderVal(e.target.value)}
                                                    placeholder="Value"
                                                    className="flex-1 px-3 py-1 border border-input bg-background rounded text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                                                />
                                                <Button size="sm" onClick={() => {
                                                    if (headerKey && headerVal) {
                                                        const currentSettings = suite.settings || { headers: {}, params: {} };
                                                        handleUpdateSettings({
                                                            ...currentSettings,
                                                            headers: { ...(currentSettings.headers || {}), [headerKey]: headerVal }
                                                        }, suite.inherit_settings, `Header '${headerKey}' added`);
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
                                                            <input disabled value={key} className="flex-1 px-3 py-1 bg-muted border border-input rounded text-sm italic text-muted-foreground" />
                                                            <input disabled value={value} className="flex-1 px-3 py-1 bg-muted border border-input rounded text-sm italic text-muted-foreground" />
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
                                                        handleUpdateSettings({ ...suite.settings, params: newParams }, suite.inherit_settings, `Parameter '${key}' removed`);
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
                                                        }, suite.inherit_settings, `Parameter '${paramKey}' added`);
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
                                                            <input disabled value={domainName} className="flex-1 px-3 py-1 bg-muted border border-input rounded text-sm italic text-muted-foreground" />
                                                            <div className="flex items-center gap-2 text-xs text-gray-500">
                                                                <label className="flex items-center gap-1 cursor-not-allowed">

                                                                    <Checkbox checked={allowHeaders} disabled /> Headers
                                                                </label>
                                                                <label className="flex items-center gap-1 cursor-not-allowed">
                                                                    <Checkbox checked={allowParams} disabled /> Params
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

                                                            <Checkbox
                                                                checked={allowHeaders}
                                                                onCheckedChange={(checked) => {
                                                                    const newDomains = [...suite.settings.allowed_domains];
                                                                    newDomains[idx] = {
                                                                        domain: domainName,
                                                                        headers: !!checked,
                                                                        params: allowParams
                                                                    };
                                                                    handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, "Domain permissions updated");
                                                                }}
                                                            /> Headers
                                                        </label>
                                                        <label className="flex items-center gap-1 cursor-pointer">
                                                            <Checkbox
                                                                checked={allowParams}
                                                                onCheckedChange={(checked) => {
                                                                    const newDomains = [...suite.settings.allowed_domains];
                                                                    newDomains[idx] = {
                                                                        domain: domainName,
                                                                        headers: allowHeaders,
                                                                        params: !!checked
                                                                    };
                                                                    handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, "Domain permissions updated");
                                                                }}
                                                            /> Params
                                                        </label>
                                                    </div>
                                                    <Button variant="ghost" size="sm" onClick={() => {
                                                        const newDomains = suite.settings.allowed_domains.filter((_: any, i: number) => i !== idx);
                                                        handleUpdateSettings({ ...suite.settings, allowed_domains: newDomains }, suite.inherit_settings, `Domain '${domainName}' removed`);
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
                                                                }, suite.inherit_settings, `Domain '${val}' added`);
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
                                            <div className="pl-4 border-l-2 border-border space-y-2">
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
                                            <div className="pl-4 border-l-2 border-border space-y-2 mt-4">
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
                    </motion.div>
                ) : null}

                {
                    activeTab === 'audit' && (
                        <motion.div
                            key="audit"
                            variants={tabVariants}
                            initial="hidden"
                            animate="visible"
                            exit="exit"
                            className="space-y-4"
                        >
                            <Card>
                                <CardHeader>
                                    <CardTitle>Audit Log</CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <div className="space-y-4">
                                        {auditLogs?.map((log: any) => (
                                            <div key={log.id} className="flex flex-col p-4 border rounded-lg bg-gray-50">
                                                <div className="flex justify-between items-start mb-2">
                                                    <div className="flex items-center gap-2">
                                                        <span className={`px-2 py-1 rounded text-xs font-bold uppercase ${log.action === 'create' ? 'bg-green-100 text-green-700' :
                                                            log.action === 'update' ? 'bg-blue-100 text-blue-700' :
                                                                log.action === 'delete' ? 'bg-red-100 text-red-700' :
                                                                    'bg-gray-100 text-gray-700'
                                                            }`}>
                                                            {log.action}
                                                        </span>
                                                        <span className="font-medium text-sm">
                                                            by {log.user?.full_name || 'Unknown User'}
                                                        </span>
                                                    </div>
                                                    <span className="text-xs text-gray-500">
                                                        {new Date(log.timestamp).toLocaleString()}
                                                    </span>
                                                </div>
                                                {log.changes && Object.keys(log.changes).length > 0 && (
                                                    <div className="mt-3 text-sm">
                                                        {log.action === 'update' ? (
                                                            <div className="border rounded-md overflow-hidden">
                                                                <table className="w-full text-left text-xs">
                                                                    <thead className="bg-gray-50 border-b">
                                                                        <tr>
                                                                            <th className="px-3 py-2 font-medium text-gray-500">Field</th>
                                                                            <th className="px-3 py-2 font-medium text-gray-500">Old Value</th>
                                                                            <th className="px-3 py-2 font-medium text-gray-500">New Value</th>
                                                                        </tr>
                                                                    </thead>
                                                                    <tbody className="divide-y">
                                                                        {Object.entries(log.changes).map(([key, val]: [string, any]) => (
                                                                            <tr key={key} className="bg-white">
                                                                                <td className="px-3 py-2 font-mono text-gray-600">{key}</td>
                                                                                <td className="px-3 py-2 text-red-600 bg-red-50/30 font-mono break-all">
                                                                                    {typeof val.old === 'object' ? JSON.stringify(val.old) : String(val.old ?? 'null')}
                                                                                </td>
                                                                                <td className="px-3 py-2 text-green-600 bg-green-50/30 font-mono break-all">
                                                                                    {typeof val.new === 'object' ? JSON.stringify(val.new) : String(val.new ?? 'null')}
                                                                                </td>
                                                                            </tr>
                                                                        ))}
                                                                    </tbody>
                                                                </table>
                                                            </div>
                                                        ) : log.action === 'create' ? (
                                                            <div className="bg-green-50 border border-green-100 rounded p-3 text-green-800 text-xs">
                                                                <span className="font-semibold">Created with initial values:</span>
                                                                <div className="mt-1 font-mono opacity-80">
                                                                    {Object.keys(log.changes).join(', ')}
                                                                </div>
                                                            </div>
                                                        ) : log.action === 'import' ? (
                                                            <div className="bg-blue-50 border border-blue-100 rounded p-3 text-blue-800 text-xs">
                                                                <span className="font-semibold">Imported data source:</span>
                                                                <div className="mt-1 font-mono opacity-80">
                                                                    Source: {log.changes.source || 'Unknown'}
                                                                </div>
                                                            </div>
                                                        ) : log.action === 'delete' ? (
                                                            <div className="bg-red-50 border border-red-100 rounded p-3 text-red-800 text-xs">
                                                                <span className="font-semibold">Deleted entity:</span>
                                                                <div className="mt-1 font-mono opacity-80">
                                                                    ID: {log.entity_id}
                                                                </div>
                                                            </div>
                                                        ) : (
                                                            <div className="mt-2 text-xs font-mono bg-white p-2 rounded border overflow-x-auto">
                                                                <div className="mb-1 text-gray-500 font-semibold">Raw Data (Action: {log.action})</div>
                                                                <pre>{JSON.stringify(log.changes, null, 2)}</pre>
                                                            </div>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        ))}
                                        {(!auditLogs || auditLogs.length === 0) && (
                                            <div className="text-center py-8 text-gray-500">
                                                No audit history available.
                                            </div>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>
                        </motion.div>
                    )
                }
            </AnimatePresence >
        </div >
    );
}
