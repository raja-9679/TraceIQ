import { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Settings as SettingsIcon, User, Bell, Database, Zap, Save, X, Loader2, KeyRound, Copy, Trash2, Plus, AlertTriangle } from 'lucide-react';
import { toast } from 'sonner';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { api, getSettings, updateSettings, getWorkspaces, type Workspace } from '@/lib/api';
import { useAuth } from '@/context/AuthContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';

// ---- API key types & calls (backend/app/api/api_keys.py) ----

interface ApiKeyRead {
    id: number;
    workspace_id: number;
    project_id: number | null;
    name: string;
    prefix: string;
    created_at: string;
    last_used_at: string | null;
    expires_at: string | null;
    revoked_at: string | null;
}

interface ApiKeyCreateResponse extends ApiKeyRead {
    // Plaintext secret — returned exactly once at creation time.
    secret: string;
}

const listApiKeys = async (workspaceId: number): Promise<ApiKeyRead[]> => {
    const response = await api.get(`/workspaces/${workspaceId}/api-keys`);
    return response.data;
};

const createApiKey = async (workspaceId: number, name: string): Promise<ApiKeyCreateResponse> => {
    const response = await api.post(`/workspaces/${workspaceId}/api-keys`, {
        name,
        workspace_id: workspaceId,
    });
    return response.data;
};

const revokeApiKey = async (workspaceId: number, keyId: number): Promise<void> => {
    await api.delete(`/workspaces/${workspaceId}/api-keys/${keyId}`);
};

const formatDate = (value: string | null): string => {
    if (!value) return '—';
    // Backend stores naive UTC timestamps; treat them as UTC.
    const iso = value.endsWith('Z') || value.includes('+') ? value : `${value}Z`;
    const date = new Date(iso);
    if (isNaN(date.getTime())) return '—';
    return date.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
};

type KeyStatus = 'active' | 'revoked' | 'expired';

const keyStatus = (key: ApiKeyRead): KeyStatus => {
    if (key.revoked_at) return 'revoked';
    if (key.expires_at && new Date(key.expires_at.endsWith('Z') ? key.expires_at : `${key.expires_at}Z`) < new Date()) {
        return 'expired';
    }
    return 'active';
};

export default function Settings() {
    const queryClient = useQueryClient();
    const [activeSection, setActiveSection] = useState('general');
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

    // General Settings
    const [theme, setTheme] = useState('light');
    const [timezone, setTimezone] = useState('UTC');
    const [dateFormat, setDateFormat] = useState('MM/DD/YYYY');

    // Test Execution Defaults
    const [defaultBrowser, setDefaultBrowser] = useState('chromium');
    const [defaultDevice, setDefaultDevice] = useState('Desktop');
    const [defaultTimeout, setDefaultTimeout] = useState(30000);
    const [autoRetry, setAutoRetry] = useState(false);
    const [maxRetries, setMaxRetries] = useState(3);
    const [parallelExecution, setParallelExecution] = useState(false);
    const [maxParallelTests, setMaxParallelTests] = useState(3);

    // Notifications
    const [emailNotifications, setEmailNotifications] = useState(false);
    const [notifyOnCompletion, setNotifyOnCompletion] = useState(true);
    const [notifyOnFailure, setNotifyOnFailure] = useState(true);
    const [dailySummary, setDailySummary] = useState(false);
    const [notificationEmail, setNotificationEmail] = useState('');

    // Storage & Retention
    const [videoRecording, setVideoRecording] = useState('on-failure');
    const [screenshotOnError, setScreenshotOnError] = useState(true);
    const [traceFiles, setTraceFiles] = useState(true);
    const [retentionPeriod, setRetentionPeriod] = useState(30);
    const [autoCleanup, setAutoCleanup] = useState(true);

    // Multi-Browser Testing
    const [multiBrowserEnabled, setMultiBrowserEnabled] = useState(false);
    const [selectedBrowsers, setSelectedBrowsers] = useState<string[]>(['chromium']);

    // Multi-Device Testing
    const [multiDeviceEnabled, setMultiDeviceEnabled] = useState(false);
    const [selectedDevices, setSelectedDevices] = useState<string[]>(['Desktop']);

    // Load settings
    const { data: settings } = useQuery({
        queryKey: ['settings'],
        queryFn: getSettings,
    });

    // Update state when settings are loaded
    useEffect(() => {
        if (settings) {
            setTheme(settings.theme);
            setTimezone(settings.timezone);
            setDateFormat(settings.date_format);
            setDefaultBrowser(settings.default_browser);
            setDefaultDevice(settings.default_device);
            setDefaultTimeout(settings.default_timeout);
            setAutoRetry(settings.auto_retry);
            setMaxRetries(settings.max_retries);
            setParallelExecution(settings.parallel_execution);
            setMaxParallelTests(settings.max_parallel_tests);
            setMultiBrowserEnabled(settings.multi_browser_enabled);
            setSelectedBrowsers(settings.selected_browsers);
            setMultiDeviceEnabled(settings.multi_device_enabled);
            setSelectedDevices(settings.selected_devices);
            setEmailNotifications(settings.email_notifications);
            setNotifyOnCompletion(settings.notify_on_completion);
            setNotifyOnFailure(settings.notify_on_failure);
            setDailySummary(settings.daily_summary);
            setNotificationEmail(settings.notification_email || '');
            setVideoRecording(settings.video_recording);
            setScreenshotOnError(settings.screenshot_on_error);
            setTraceFiles(settings.trace_files);
            setRetentionPeriod(settings.retention_period);
            setAutoCleanup(settings.auto_cleanup);
        }
    }, [settings]);

    const saveMutation = useMutation({
        mutationFn: updateSettings,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['settings'] });
            setHasUnsavedChanges(false);
            toast.success('Settings saved successfully');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to save settings');
        }
    });

    const handleSave = () => {
        saveMutation.mutate({
            theme,
            timezone,
            date_format: dateFormat,
            default_browser: defaultBrowser,
            default_device: defaultDevice,
            default_timeout: defaultTimeout,
            auto_retry: autoRetry,
            max_retries: maxRetries,
            parallel_execution: parallelExecution,
            max_parallel_tests: maxParallelTests,
            multi_browser_enabled: multiBrowserEnabled,
            selected_browsers: selectedBrowsers,
            multi_device_enabled: multiDeviceEnabled,
            selected_devices: selectedDevices,
            email_notifications: emailNotifications,
            notify_on_completion: notifyOnCompletion,
            notify_on_failure: notifyOnFailure,
            daily_summary: dailySummary,
            notification_email: notificationEmail || null,
            video_recording: videoRecording,
            screenshot_on_error: screenshotOnError,
            trace_files: traceFiles,
            retention_period: retentionPeriod,
            auto_cleanup: autoCleanup,
        });
    };

    const handleCancel = () => {
        // Reload settings from server
        if (settings) {
            setTheme(settings.theme);
            setTimezone(settings.timezone);
            setDateFormat(settings.date_format);
            setDefaultBrowser(settings.default_browser);
            setDefaultDevice(settings.default_device);
            setDefaultTimeout(settings.default_timeout);
            setAutoRetry(settings.auto_retry);
            setMaxRetries(settings.max_retries);
            setParallelExecution(settings.parallel_execution);
            setMaxParallelTests(settings.max_parallel_tests);
            setMultiBrowserEnabled(settings.multi_browser_enabled);
            setSelectedBrowsers(settings.selected_browsers);
            setEmailNotifications(settings.email_notifications);
            setNotifyOnCompletion(settings.notify_on_completion);
            setNotifyOnFailure(settings.notify_on_failure);
            setDailySummary(settings.daily_summary);
            setNotificationEmail(settings.notification_email || '');
            setVideoRecording(settings.video_recording);
            setScreenshotOnError(settings.screenshot_on_error);
            setTraceFiles(settings.trace_files);
            setRetentionPeriod(settings.retention_period);
            setAutoCleanup(settings.auto_cleanup);
        }
        setHasUnsavedChanges(false);
    };

    const sections = [
        { id: 'general', name: 'General', icon: SettingsIcon },
        { id: 'execution', name: 'Test Execution', icon: Zap },
        { id: 'notifications', name: 'Notifications', icon: Bell },
        { id: 'storage', name: 'Storage & Retention', icon: Database },
        { id: 'account', name: 'Account', icon: User },
    ];

    return (
        <div className="min-h-screen bg-gray-50">
            <div className="max-w-7xl mx-auto px-4 py-8">
                <div className="mb-8">
                    <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
                    <p className="text-gray-500 mt-1">Manage your application preferences and configurations</p>
                </div>

                <div className="grid grid-cols-12 gap-6">
                    {/* Sidebar Navigation */}
                    <div className="col-span-3">
                        <Card>
                            <CardContent className="p-4">
                                <nav className="space-y-1">
                                    {sections.map((section) => {
                                        const Icon = section.icon;
                                        return (
                                            <button
                                                key={section.id}
                                                onClick={() => setActiveSection(section.id)}
                                                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${activeSection === section.id
                                                    ? 'bg-primary text-white'
                                                    : 'text-gray-700 hover:bg-gray-100'
                                                    }`}
                                            >
                                                <Icon className="h-4 w-4" />
                                                {section.name}
                                            </button>
                                        );
                                    })}
                                </nav>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Main Content */}
                    <div className="col-span-9 space-y-6">
                        {/* General Settings */}
                        {activeSection === 'general' && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>General Settings</CardTitle>
                                    <CardDescription>Configure your general application preferences</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">Theme</label>
                                        <Select
                                            value={theme}
                                            onValueChange={(value) => {
                                                setTheme(value);
                                                setHasUnsavedChanges(true);
                                            }}
                                        >
                                            <SelectTrigger className="w-full">
                                                <SelectValue placeholder="Select theme" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="light">Light</SelectItem>
                                                <SelectItem value="dark">Dark</SelectItem>
                                                <SelectItem value="auto">Auto (System)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">Timezone</label>
                                        <Select
                                            value={timezone}
                                            onValueChange={(value) => {
                                                setTimezone(value);
                                                setHasUnsavedChanges(true);
                                            }}
                                        >
                                            <SelectTrigger className="w-full">
                                                <SelectValue placeholder="Select timezone" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="UTC">UTC</SelectItem>
                                                <SelectItem value="America/New_York">Eastern Time</SelectItem>
                                                <SelectItem value="America/Chicago">Central Time</SelectItem>
                                                <SelectItem value="America/Los_Angeles">Pacific Time</SelectItem>
                                                <SelectItem value="Asia/Kolkata">India Standard Time</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">Date Format</label>
                                        <Select
                                            value={dateFormat}
                                            onValueChange={(value) => {
                                                setDateFormat(value);
                                                setHasUnsavedChanges(true);
                                            }}
                                        >
                                            <SelectTrigger className="w-full">
                                                <SelectValue placeholder="Select date format" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="MM/DD/YYYY">MM/DD/YYYY</SelectItem>
                                                <SelectItem value="DD/MM/YYYY">DD/MM/YYYY</SelectItem>
                                                <SelectItem value="YYYY-MM-DD">YYYY-MM-DD</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </CardContent>
                            </Card>
                        )}

                        {/* Test Execution Defaults */}
                        {activeSection === 'execution' && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>Test Execution Defaults</CardTitle>
                                    <CardDescription>Set default values for test execution</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    <div className="grid grid-cols-2 gap-4">
                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 mb-2">Default Browser</label>
                                            <Select
                                                value={defaultBrowser}
                                                onValueChange={(value) => {
                                                    setDefaultBrowser(value);
                                                    setHasUnsavedChanges(true);
                                                }}
                                            >
                                                <SelectTrigger className="w-full">
                                                    <SelectValue placeholder="Select browser" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="chromium">Chromium</SelectItem>
                                                    <SelectItem value="firefox">Firefox</SelectItem>
                                                    <SelectItem value="webkit">WebKit</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>

                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 mb-2">Default Device</label>
                                            <Select
                                                value={defaultDevice}
                                                onValueChange={(value) => {
                                                    setDefaultDevice(value);
                                                    setHasUnsavedChanges(true);
                                                }}
                                            >
                                                <SelectTrigger className="w-full">
                                                    <SelectValue placeholder="Select device" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="Desktop">Desktop</SelectItem>
                                                    <SelectItem value="Mobile (Generic)">Mobile (Generic)</SelectItem>
                                                    <SelectItem value="iPhone 13">iPhone 13</SelectItem>
                                                    <SelectItem value="Pixel 5">Pixel 5</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>

                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">
                                            Default Timeout (ms)
                                        </label>
                                        <input
                                            type="number"
                                            value={defaultTimeout}
                                            onChange={(e) => {
                                                setDefaultTimeout(Number(e.target.value));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                            min="1000"
                                            max="120000"
                                            step="1000"
                                        />
                                        <p className="text-xs text-gray-500 mt-1">Timeout for individual test steps (1-120 seconds)</p>
                                    </div>

                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Auto-retry Failed Tests</p>
                                            <p className="text-sm text-gray-500">Automatically retry tests that fail</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={autoRetry}
                                                onChange={(e) => {
                                                    setAutoRetry(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>

                                    {autoRetry && (
                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 mb-2">Max Retry Attempts</label>
                                            <input
                                                type="number"
                                                value={maxRetries}
                                                onChange={(e) => {
                                                    setMaxRetries(Number(e.target.value));
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                                min="1"
                                                max="5"
                                            />
                                        </div>
                                    )}

                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Parallel Execution</p>
                                            <p className="text-sm text-gray-500">Run multiple tests simultaneously</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={parallelExecution}
                                                onChange={(e) => {
                                                    setParallelExecution(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>

                                    {parallelExecution && (
                                        <div>
                                            <label className="block text-sm font-medium text-gray-700 mb-2">Max Parallel Tests</label>
                                            <input
                                                type="number"
                                                value={maxParallelTests}
                                                onChange={(e) => {
                                                    setMaxParallelTests(Number(e.target.value));
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                                min="1"
                                                max="10"
                                            />
                                        </div>
                                    )}

                                    <div className="border-t pt-6">
                                        <h3 className="text-sm font-medium text-gray-700 mb-4">Multi-Browser Testing</h3>

                                        <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg mb-4">
                                            <div>
                                                <p className="font-medium text-gray-900">Enable Multi-Browser Execution</p>
                                                <p className="text-sm text-gray-500">Run tests across multiple browsers simultaneously</p>
                                            </div>
                                            <label className="relative inline-flex items-center cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={multiBrowserEnabled}
                                                    onChange={(e) => {
                                                        setMultiBrowserEnabled(e.target.checked);
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                    className="sr-only peer"
                                                />
                                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                            </label>
                                        </div>

                                        <div className="space-y-3">
                                            <p className="text-sm font-medium text-gray-700">Select browsers to test:</p>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedBrowsers.includes('chromium')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedBrowsers([...selectedBrowsers, 'chromium']);
                                                        } else {
                                                            setSelectedBrowsers(selectedBrowsers.filter(b => b !== 'chromium'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-blue-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-blue-700">Ch</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">Chromium</p>
                                                        <p className="text-xs text-gray-500">Chrome, Edge, Brave</p>
                                                    </div>
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedBrowsers.includes('firefox')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedBrowsers([...selectedBrowsers, 'firefox']);
                                                        } else {
                                                            setSelectedBrowsers(selectedBrowsers.filter(b => b !== 'firefox'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-orange-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-orange-700">Fx</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">Firefox</p>
                                                        <p className="text-xs text-gray-500">Mozilla Firefox</p>
                                                    </div>
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedBrowsers.includes('webkit')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedBrowsers([...selectedBrowsers, 'webkit']);
                                                        } else {
                                                            setSelectedBrowsers(selectedBrowsers.filter(b => b !== 'webkit'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-purple-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-purple-700">Wk</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">WebKit</p>
                                                        <p className="text-xs text-gray-500">Safari engine</p>
                                                    </div>
                                                </div>
                                            </label>
                                        </div>

                                        <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                                            <p className="text-sm text-blue-800">
                                                <strong>Note:</strong> When multi-browser testing is enabled, each test will run on all selected browsers. This multiplies the total number of test runs.
                                            </p>
                                        </div>
                                    </div>

                                    <div className="border-t pt-6">
                                        <h3 className="text-sm font-medium text-gray-700 mb-4">Multi-Device Testing</h3>

                                        <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg mb-4">
                                            <div>
                                                <p className="font-medium text-gray-900">Enable Multi-Device Execution</p>
                                                <p className="text-sm text-gray-500">Run tests across multiple devices simultaneously</p>
                                            </div>
                                            <label className="relative inline-flex items-center cursor-pointer">
                                                <input
                                                    type="checkbox"
                                                    checked={multiDeviceEnabled}
                                                    onChange={(e) => {
                                                        setMultiDeviceEnabled(e.target.checked);
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                    className="sr-only peer"
                                                />
                                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                            </label>
                                        </div>

                                        <div className="space-y-3">
                                            <p className="text-sm font-medium text-gray-700">Select devices to test:</p>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedDevices.includes('Desktop')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedDevices([...selectedDevices, 'Desktop']);
                                                        } else {
                                                            setSelectedDevices(selectedDevices.filter(d => d !== 'Desktop'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-gray-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-gray-700">🖥️</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">Desktop</p>
                                                        <p className="text-xs text-gray-500">Standard desktop viewport</p>
                                                    </div>
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedDevices.includes('Mobile (Generic)')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedDevices([...selectedDevices, 'Mobile (Generic)']);
                                                        } else {
                                                            setSelectedDevices(selectedDevices.filter(d => d !== 'Mobile (Generic)'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-green-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-green-700">📱</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">Mobile (Generic)</p>
                                                        <p className="text-xs text-gray-500">Generic mobile viewport</p>
                                                    </div>
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedDevices.includes('iPhone 13')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedDevices([...selectedDevices, 'iPhone 13']);
                                                        } else {
                                                            setSelectedDevices(selectedDevices.filter(d => d !== 'iPhone 13'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-blue-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-blue-700">🍎</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">iPhone 13</p>
                                                        <p className="text-xs text-gray-500">Apple iPhone 13</p>
                                                    </div>
                                                </div>
                                            </label>

                                            <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                <Checkbox
                                                    checked={selectedDevices.includes('Pixel 5')}
                                                    onCheckedChange={(checked) => {
                                                        if (checked) {
                                                            setSelectedDevices([...selectedDevices, 'Pixel 5']);
                                                        } else {
                                                            setSelectedDevices(selectedDevices.filter(d => d !== 'Pixel 5'));
                                                        }
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                />
                                                <div className="flex items-center gap-2">
                                                    <div className="w-8 h-8 rounded bg-red-100 flex items-center justify-center">
                                                        <span className="text-xs font-bold text-red-700">🤖</span>
                                                    </div>
                                                    <div>
                                                        <p className="font-medium text-gray-900">Pixel 5</p>
                                                        <p className="text-xs text-gray-500">Google Pixel 5</p>
                                                    </div>
                                                </div>
                                            </label>
                                        </div>

                                        <div className="mt-4 p-3 bg-blue-50 border border-blue-200 rounded-lg">
                                            <p className="text-sm text-blue-800">
                                                <strong>Note:</strong> When multi-device testing is enabled, each test will run on all selected devices. This multiplies the total number of test runs.
                                            </p>
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        )}

                        {/* Notifications */}
                        {activeSection === 'notifications' && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>Notification Settings</CardTitle>
                                    <CardDescription>Configure how and when you receive notifications</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Email Notifications</p>
                                            <p className="text-sm text-gray-500">Enable email notifications for test events</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={emailNotifications}
                                                onChange={(e) => {
                                                    setEmailNotifications(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>

                                    {emailNotifications && (
                                        <>
                                            <div>
                                                <label className="block text-sm font-medium text-gray-700 mb-2">Notification Email</label>
                                                <input
                                                    type="email"
                                                    value={notificationEmail}
                                                    onChange={(e) => {
                                                        setNotificationEmail(e.target.value);
                                                        setHasUnsavedChanges(true);
                                                    }}
                                                    placeholder="your@email.com"
                                                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                                />
                                            </div>

                                            <div className="space-y-3">
                                                <p className="text-sm font-medium text-gray-700">Notify me when:</p>

                                                <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                    <Checkbox
                                                        checked={notifyOnCompletion}
                                                        onCheckedChange={(checked) => {
                                                            setNotifyOnCompletion(checked as boolean);
                                                            setHasUnsavedChanges(true);
                                                        }}
                                                    />
                                                    <div>
                                                        <p className="font-medium text-gray-900">Test Completion</p>
                                                        <p className="text-sm text-gray-500">Any test run completes</p>
                                                    </div>
                                                </label>

                                                <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                    <Checkbox
                                                        checked={notifyOnFailure}
                                                        onCheckedChange={(checked) => {
                                                            setNotifyOnFailure(checked as boolean);
                                                            setHasUnsavedChanges(true);
                                                        }}
                                                    />
                                                    <div>
                                                        <p className="font-medium text-gray-900">Test Failure</p>
                                                        <p className="text-sm text-gray-500">Only when tests fail</p>
                                                    </div>
                                                </label>

                                                <label className="flex items-center gap-3 p-3 border border-gray-200 rounded-lg cursor-pointer hover:bg-gray-50">
                                                    <Checkbox
                                                        checked={dailySummary}
                                                        onCheckedChange={(checked) => {
                                                            setDailySummary(checked as boolean);
                                                            setHasUnsavedChanges(true);
                                                        }}
                                                    />
                                                    <div>
                                                        <p className="font-medium text-gray-900">Daily Summary</p>
                                                        <p className="text-sm text-gray-500">Daily report of all test activity</p>
                                                    </div>
                                                </label>
                                            </div>
                                        </>
                                    )}
                                </CardContent>
                            </Card>
                        )}

                        {/* Storage & Retention */}
                        {activeSection === 'storage' && (
                            <Card>
                                <CardHeader>
                                    <CardTitle>Storage & Retention</CardTitle>
                                    <CardDescription>Manage test artifacts and data retention policies</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">Video Recording</label>
                                        <Select
                                            value={videoRecording}
                                            onValueChange={(value) => {
                                                setVideoRecording(value);
                                                setHasUnsavedChanges(true);
                                            }}
                                        >
                                            <SelectTrigger className="w-full">
                                                <SelectValue placeholder="Select recording preference" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="always">Always Record</SelectItem>
                                                <SelectItem value="on-failure">Only on Failure</SelectItem>
                                                <SelectItem value="never">Never Record</SelectItem>
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs text-gray-500 mt-1">When to record video of test execution</p>
                                    </div>

                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Screenshot on Error</p>
                                            <p className="text-sm text-gray-500">Capture screenshot when test fails</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={screenshotOnError}
                                                onChange={(e) => {
                                                    setScreenshotOnError(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>

                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Playwright Trace Files</p>
                                            <p className="text-sm text-gray-500">Enable detailed trace files for debugging</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={traceFiles}
                                                onChange={(e) => {
                                                    setTraceFiles(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>

                                    <div>
                                        <label className="block text-sm font-medium text-gray-700 mb-2">
                                            Retention Period (days)
                                        </label>
                                        <input
                                            type="number"
                                            value={retentionPeriod}
                                            onChange={(e) => {
                                                setRetentionPeriod(Number(e.target.value));
                                                setHasUnsavedChanges(true);
                                            }}
                                            className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                                            min="1"
                                            max="365"
                                        />
                                        <p className="text-xs text-gray-500 mt-1">How long to keep test runs and artifacts</p>
                                    </div>

                                    <div className="flex items-center justify-between p-4 bg-gray-50 rounded-lg">
                                        <div>
                                            <p className="font-medium text-gray-900">Auto-cleanup</p>
                                            <p className="text-sm text-gray-500">Automatically delete old test runs</p>
                                        </div>
                                        <label className="relative inline-flex items-center cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={autoCleanup}
                                                onChange={(e) => {
                                                    setAutoCleanup(e.target.checked);
                                                    setHasUnsavedChanges(true);
                                                }}
                                                className="sr-only peer"
                                            />
                                            <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                                        </label>
                                    </div>
                                </CardContent>
                            </Card>
                        )}

                        {/* Account Settings */}
                        {activeSection === 'account' && <AccountSection />}

                        {/* Save/Cancel Actions */}
                        {hasUnsavedChanges && (
                            <div className="sticky bottom-0 bg-white border-t border-gray-200 p-4 flex items-center justify-between shadow-lg rounded-lg">
                                <p className="text-sm text-gray-600">You have unsaved changes</p>
                                <div className="flex gap-3">
                                    <Button variant="outline" onClick={handleCancel} disabled={saveMutation.isPending}>
                                        <X className="h-4 w-4 mr-2" />
                                        Cancel
                                    </Button>
                                    <Button onClick={handleSave} disabled={saveMutation.isPending}>
                                        {saveMutation.isPending ? (
                                            <>
                                                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                                Saving...
                                            </>
                                        ) : (
                                            <>
                                                <Save className="h-4 w-4 mr-2" />
                                                Save Changes
                                            </>
                                        )}
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}

// ---------------------------------------------------------------------------
// Account tab
// ---------------------------------------------------------------------------

function AccountSection() {
    const { user } = useAuth();

    return (
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <CardTitle>Account</CardTitle>
                    <CardDescription>Your profile information</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                        <div>
                            <p className="text-sm font-medium text-gray-700 mb-1">Full Name</p>
                            <p className="text-sm text-gray-900 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg">
                                {user?.full_name || '—'}
                            </p>
                        </div>
                        <div>
                            <p className="text-sm font-medium text-gray-700 mb-1">Email</p>
                            <p className="text-sm text-gray-900 px-3 py-2 bg-gray-50 border border-gray-200 rounded-lg">
                                {user?.email || '—'}
                            </p>
                        </div>
                    </div>
                    <p className="text-xs text-gray-500">
                        Profile and password changes are not yet supported from this screen. Contact your workspace administrator if you need to update them.
                    </p>
                </CardContent>
            </Card>

            <ApiKeysSection />
        </div>
    );
}

// ---------------------------------------------------------------------------
// API key management
// ---------------------------------------------------------------------------

function ApiKeysSection() {
    const queryClient = useQueryClient();

    const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null);
    const [createOpen, setCreateOpen] = useState(false);
    const [newKeyName, setNewKeyName] = useState('');
    // Set only immediately after a successful POST — the one time the secret exists client-side.
    const [createdKey, setCreatedKey] = useState<ApiKeyCreateResponse | null>(null);
    const [revokeTarget, setRevokeTarget] = useState<ApiKeyRead | null>(null);

    const { data: workspaces } = useQuery<Workspace[]>({
        queryKey: ['workspaces'],
        queryFn: getWorkspaces,
    });

    useEffect(() => {
        if (workspaces && workspaces.length > 0 && selectedWorkspaceId === null) {
            setSelectedWorkspaceId(workspaces[0].id);
        }
    }, [workspaces, selectedWorkspaceId]);

    const { data: keys, isLoading: keysLoading } = useQuery<ApiKeyRead[]>({
        queryKey: ['apiKeys', selectedWorkspaceId],
        queryFn: () => listApiKeys(selectedWorkspaceId!),
        enabled: selectedWorkspaceId !== null,
    });

    const createMutation = useMutation({
        mutationFn: (name: string) => createApiKey(selectedWorkspaceId!, name),
        onSuccess: (data) => {
            setCreatedKey(data);
            setNewKeyName('');
            queryClient.invalidateQueries({ queryKey: ['apiKeys', selectedWorkspaceId] });
            toast.success('API key created');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to create API key');
        },
    });

    const revokeMutation = useMutation({
        mutationFn: (keyId: number) => revokeApiKey(selectedWorkspaceId!, keyId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['apiKeys', selectedWorkspaceId] });
            toast.success('API key revoked');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to revoke API key');
        },
    });

    const copySecret = async (secret: string) => {
        try {
            await navigator.clipboard.writeText(secret);
            toast.success('Copied to clipboard');
        } catch {
            toast.error('Could not copy — select the key text and copy it manually');
        }
    };

    const closeCreateDialog = () => {
        setCreateOpen(false);
        setNewKeyName('');
        setCreatedKey(null);
    };

    const statusBadge = (key: ApiKeyRead) => {
        const status = keyStatus(key);
        if (status === 'revoked') return <Badge variant="destructive">Revoked</Badge>;
        if (status === 'expired') return <Badge variant="secondary">Expired</Badge>;
        return <Badge className="bg-green-100 text-green-800 hover:bg-green-100">Active</Badge>;
    };

    const mcpConfigSnippet = `{
  "mcpServers": {
    "traceiq": {
      "type": "http",
      "url": "http://your-traceiq-host:8088/mcp",
      "headers": { "X-API-Key": "tiq_xxxxxxxxxxxxxxxxxxxxxxxx" }
    }
  }
}`;

    return (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between gap-4">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <KeyRound className="h-5 w-5" />
                            API Keys
                        </CardTitle>
                        <CardDescription>
                            Workspace-scoped keys for programmatic access (CI pipelines, AI agents, the MCP server)
                        </CardDescription>
                    </div>
                    <Button
                        onClick={() => setCreateOpen(true)}
                        disabled={selectedWorkspaceId === null}
                    >
                        <Plus className="h-4 w-4 mr-2" />
                        Create API key
                    </Button>
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                {workspaces && workspaces.length > 1 && (
                    <div className="max-w-xs">
                        <label className="block text-sm font-medium text-gray-700 mb-2">Workspace</label>
                        <Select
                            value={selectedWorkspaceId !== null ? String(selectedWorkspaceId) : undefined}
                            onValueChange={(value) => setSelectedWorkspaceId(Number(value))}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select workspace" />
                            </SelectTrigger>
                            <SelectContent>
                                {workspaces.map((ws) => (
                                    <SelectItem key={ws.id} value={String(ws.id)}>{ws.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                )}

                {workspaces && workspaces.length === 0 ? (
                    <p className="text-sm text-gray-500">
                        You are not a member of any workspace. Join or create a workspace to manage API keys.
                    </p>
                ) : keysLoading ? (
                    <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading API keys...
                    </div>
                ) : keys && keys.length > 0 ? (
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Key</TableHead>
                                <TableHead>Created</TableHead>
                                <TableHead>Last used</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="w-[80px]"></TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {keys.map((key) => (
                                <TableRow key={key.id}>
                                    <TableCell className="font-medium">{key.name}</TableCell>
                                    <TableCell>
                                        <code className="text-xs bg-gray-100 px-2 py-1 rounded">{key.prefix}…</code>
                                    </TableCell>
                                    <TableCell className="text-sm text-gray-600">{formatDate(key.created_at)}</TableCell>
                                    <TableCell className="text-sm text-gray-600">
                                        {key.last_used_at ? formatDate(key.last_used_at) : 'Never'}
                                    </TableCell>
                                    <TableCell>{statusBadge(key)}</TableCell>
                                    <TableCell>
                                        {keyStatus(key) !== 'revoked' && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="text-red-600 hover:text-red-700 hover:bg-red-50"
                                                onClick={() => setRevokeTarget(key)}
                                                disabled={revokeMutation.isPending}
                                                title="Revoke key"
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                ) : (
                    <p className="text-sm text-gray-500 py-2">
                        No API keys yet. Create one to let CI pipelines or AI agents call TraceIQ.
                    </p>
                )}

                <div className="border-t pt-6">
                    <h3 className="text-sm font-medium text-gray-700 mb-2">Connect an IDE via MCP</h3>
                    <p className="text-sm text-gray-500 mb-3">
                        Point your IDE's MCP config (Claude Code <code className="text-xs bg-gray-100 px-1 rounded">.mcp.json</code>,
                        Cursor <code className="text-xs bg-gray-100 px-1 rounded">.cursor/mcp.json</code>,
                        VS Code <code className="text-xs bg-gray-100 px-1 rounded">.vscode/mcp.json</code>) at the
                        TraceIQ MCP server and authenticate with an API key:
                    </p>
                    <pre className="text-xs bg-gray-900 text-gray-100 p-4 rounded-lg overflow-x-auto">
                        <code>{mcpConfigSnippet}</code>
                    </pre>
                    <p className="text-xs text-gray-500 mt-2">
                        Replace <code className="bg-gray-100 px-1 rounded">your-traceiq-host</code> with this server's
                        hostname and the <code className="bg-gray-100 px-1 rounded">X-API-Key</code> value with a key
                        created above. The MCP server listens on port 8088.
                    </p>
                </div>
            </CardContent>

            {/* Create key dialog */}
            <Dialog open={createOpen} onOpenChange={(open) => { if (!open) closeCreateDialog(); }}>
                <DialogContent>
                    {createdKey ? (
                        <>
                            <DialogHeader>
                                <DialogTitle>API key created</DialogTitle>
                                <DialogDescription>
                                    Copy your new API key now. For security reasons it will not be shown again.
                                </DialogDescription>
                            </DialogHeader>
                            <div className="space-y-3">
                                <div className="flex items-start gap-2 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                                    <AlertTriangle className="h-4 w-4 text-amber-600 mt-0.5 shrink-0" />
                                    <p className="text-sm text-amber-800">
                                        This is the only time the full key is displayed. Store it somewhere safe —
                                        if you lose it, revoke this key and create a new one.
                                    </p>
                                </div>
                                <div className="flex items-center gap-2">
                                    <code className="flex-1 text-sm bg-gray-100 px-3 py-2 rounded-lg break-all select-all">
                                        {createdKey.secret}
                                    </code>
                                    <Button variant="outline" size="sm" onClick={() => copySecret(createdKey.secret)}>
                                        <Copy className="h-4 w-4 mr-2" />
                                        Copy
                                    </Button>
                                </div>
                            </div>
                            <DialogFooter>
                                <Button onClick={closeCreateDialog}>Done</Button>
                            </DialogFooter>
                        </>
                    ) : (
                        <>
                            <DialogHeader>
                                <DialogTitle>Create API key</DialogTitle>
                                <DialogDescription>
                                    The key is scoped to the selected workspace. Give it a name that identifies its user (e.g. "ci-pipeline", "claude-code").
                                </DialogDescription>
                            </DialogHeader>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Key name</label>
                                <Input
                                    value={newKeyName}
                                    onChange={(e) => setNewKeyName(e.target.value)}
                                    placeholder="e.g. ci-pipeline"
                                    autoFocus
                                    onKeyDown={(e) => {
                                        if (e.key === 'Enter' && newKeyName.trim() && !createMutation.isPending) {
                                            createMutation.mutate(newKeyName.trim());
                                        }
                                    }}
                                />
                            </div>
                            <DialogFooter>
                                <Button variant="outline" onClick={closeCreateDialog} disabled={createMutation.isPending}>
                                    Cancel
                                </Button>
                                <Button
                                    onClick={() => createMutation.mutate(newKeyName.trim())}
                                    disabled={!newKeyName.trim() || createMutation.isPending || selectedWorkspaceId === null}
                                >
                                    {createMutation.isPending ? (
                                        <>
                                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                            Creating...
                                        </>
                                    ) : (
                                        'Create key'
                                    )}
                                </Button>
                            </DialogFooter>
                        </>
                    )}
                </DialogContent>
            </Dialog>

            {/* Revoke confirmation */}
            <AlertDialog open={revokeTarget !== null} onOpenChange={(open) => { if (!open) setRevokeTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Revoke API key?</AlertDialogTitle>
                        <AlertDialogDescription>
                            {revokeTarget && (
                                <>
                                    "{revokeTarget.name}" ({revokeTarget.prefix}…) will stop working immediately.
                                    Anything using this key — CI jobs, agents, MCP connections — will lose access.
                                    This cannot be undone.
                                </>
                            )}
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            className="bg-red-600 hover:bg-red-700"
                            onClick={() => {
                                if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
                                setRevokeTarget(null);
                            }}
                        >
                            Revoke key
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </Card>
    );
}
