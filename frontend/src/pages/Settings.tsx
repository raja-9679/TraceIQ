import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Loader2, KeyRound, Copy, Trash2, Plus, AlertTriangle } from 'lucide-react';
import InstanceSettingsSection from '@/components/settings/InstanceSettingsSection';
import { toast } from 'sonner';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
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
import { api, getSettings, updateSettings, getWorkspaces, type Workspace } from '@/lib/api';
import { mfaApi } from '@/api/mfa';
import { QRCodeSVG } from 'qrcode.react';
import { useAuth } from '@/context/AuthContext';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { PageHeader, Pill } from '@/components/redesign/primitives';
import {
    CheckRow, FieldRow, RowGroup, SectionNav, SettingsCard, inputClass, warnCalloutClass,
} from '@/components/redesign-local/groupD/settingsForm';

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

type SectionId = 'general' | 'execution' | 'notifications' | 'storage' | 'account' | 'security' | 'instance';

const BROWSER_OPTIONS: { id: string; name: string; hint: string }[] = [
    { id: 'chromium', name: 'Chromium', hint: 'Chrome, Edge, Brave' },
    { id: 'firefox', name: 'Firefox', hint: 'Mozilla Firefox' },
    { id: 'webkit', name: 'WebKit', hint: 'Safari engine' },
];

const DEVICE_OPTIONS: { id: string; name: string; hint: string }[] = [
    { id: 'Desktop', name: 'Desktop', hint: 'Standard desktop viewport' },
    { id: 'Mobile (Generic)', name: 'Mobile (Generic)', hint: 'Generic mobile viewport' },
    { id: 'iPhone 13', name: 'iPhone 13', hint: 'Apple iPhone 13' },
    { id: 'Pixel 5', name: 'Pixel 5', hint: 'Google Pixel 5' },
];

const noteClass = 'rounded-2xl bg-background px-4 py-3 text-[13px] leading-relaxed text-ink-soft';
const selectTriggerClass = 'h-10 w-full rounded-full';

export default function Settings() {
    const queryClient = useQueryClient();
    const [activeSection, setActiveSection] = useState<SectionId>('general');
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

    // General Settings
    const [theme, setTheme] = useState('light');
    // UI feature flags — persisted to localStorage immediately (per-browser),
    // independent of the Save button.
    const [aiGeneration, setAiGeneration] = useState(
        () => localStorage.getItem('traceiq.ui.aiGeneration') === 'on'
    );
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

    // Small helper so every preference control marks the form dirty, as before.
    const dirty = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setHasUnsavedChanges(true); };

    const toggleIn = (list: string[], id: string, on: boolean) =>
        on ? [...list, id] : list.filter((x) => x !== id);

    const sections: { id: SectionId; name: string }[] = [
        { id: 'general', name: 'General' },
        { id: 'execution', name: 'Test execution' },
        { id: 'notifications', name: 'Notifications' },
        { id: 'storage', name: 'Storage and retention' },
        { id: 'account', name: 'Account and API keys' },
        { id: 'security', name: 'Two-factor sign-in' },
        // Tenant-admin only; the section itself shows an access message to
        // non-admins (the API 403s), matching the Tenant Admin page pattern.
        { id: 'instance', name: 'Instance (admin)' },
    ];

    // Every preference section saves through the same request, so each card's
    // Save sends the whole preference set — exactly what the old sticky bar did.
    const saveFooter = (
        <>
            <Button onClick={handleSave} disabled={saveMutation.isPending || !hasUnsavedChanges}>
                {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {saveMutation.isPending ? 'Saving…' : 'Save changes'}
            </Button>
            {hasUnsavedChanges && (
                <Button variant="outline" onClick={handleCancel} disabled={saveMutation.isPending}>
                    Discard
                </Button>
            )}
            <span className="text-xs text-ink-soft" aria-live="polite">
                {hasUnsavedChanges ? 'You have unsaved changes.' : 'No unsaved changes.'}
            </span>
        </>
    );

    return (
        <div className="flex flex-col gap-6">
            <PageHeader title="Settings" subtitle="Your preferences, account, sign-in security and instance configuration." />

            <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                <aside className="lg:sticky lg:top-6 lg:w-60 lg:flex-none">
                    <SectionNav label="Settings sections" sections={sections} active={activeSection} onChange={setActiveSection} />
                </aside>

                <div className="flex min-w-0 flex-1 flex-col gap-5">
                    {/* General Settings */}
                    {activeSection === 'general' && (
                        <>
                            <SettingsCard
                                labelledBy="settings-display"
                                title="Display"
                                description="How TraceIQ looks and how it shows times and dates."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <FieldRow label="Theme" htmlFor="set-theme">
                                        <Select value={theme} onValueChange={dirty(setTheme)}>
                                            <SelectTrigger id="set-theme" className={selectTriggerClass}>
                                                <SelectValue placeholder="Select theme" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="light">Light</SelectItem>
                                                <SelectItem value="dark">Dark</SelectItem>
                                                <SelectItem value="auto">Auto (System)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </FieldRow>
                                    <FieldRow label="Timezone" htmlFor="set-timezone">
                                        <Select value={timezone} onValueChange={dirty(setTimezone)}>
                                            <SelectTrigger id="set-timezone" className={selectTriggerClass}>
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
                                    </FieldRow>
                                    <FieldRow label="Date format" htmlFor="set-date-format">
                                        <Select value={dateFormat} onValueChange={dirty(setDateFormat)}>
                                            <SelectTrigger id="set-date-format" className={selectTriggerClass}>
                                                <SelectValue placeholder="Select date format" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="MM/DD/YYYY">MM/DD/YYYY</SelectItem>
                                                <SelectItem value="DD/MM/YYYY">DD/MM/YYYY</SelectItem>
                                                <SelectItem value="YYYY-MM-DD">YYYY-MM-DD</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </FieldRow>
                                </div>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-features"
                                title="Features in this browser"
                                description="Applies straight away and is remembered by this browser only — no Save needed."
                            >
                                <CheckRow
                                    id="set-ai-generation"
                                    label="AI test generation"
                                    hint="Show the Generate button on suite pages. It drafts test cases with the configured AI provider."
                                    checked={aiGeneration}
                                    onChange={(v) => {
                                        setAiGeneration(v);
                                        localStorage.setItem('traceiq.ui.aiGeneration', v ? 'on' : 'off');
                                    }}
                                />
                            </SettingsCard>
                        </>
                    )}

                    {/* Test Execution Defaults */}
                    {activeSection === 'execution' && (
                        <>
                            <SettingsCard
                                labelledBy="settings-exec-defaults"
                                title="Run defaults"
                                description="Used when a run does not choose its own browser, device or timeout."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <FieldRow label="Default browser" htmlFor="set-default-browser">
                                        <Select value={defaultBrowser} onValueChange={dirty(setDefaultBrowser)}>
                                            <SelectTrigger id="set-default-browser" className={selectTriggerClass}>
                                                <SelectValue placeholder="Select browser" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="chromium">Chromium</SelectItem>
                                                <SelectItem value="firefox">Firefox</SelectItem>
                                                <SelectItem value="webkit">WebKit</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </FieldRow>
                                    <FieldRow label="Default device" htmlFor="set-default-device">
                                        <Select value={defaultDevice} onValueChange={dirty(setDefaultDevice)}>
                                            <SelectTrigger id="set-default-device" className={selectTriggerClass}>
                                                <SelectValue placeholder="Select device" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="Desktop">Desktop</SelectItem>
                                                <SelectItem value="Mobile (Generic)">Mobile (Generic)</SelectItem>
                                                <SelectItem value="iPhone 13">iPhone 13</SelectItem>
                                                <SelectItem value="Pixel 5">Pixel 5</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </FieldRow>
                                    <FieldRow label="Step timeout (ms)" htmlFor="set-default-timeout" hint="Timeout for each test step, 1 to 120 seconds.">
                                        <Input
                                            id="set-default-timeout"
                                            type="number"
                                            className={inputClass}
                                            value={defaultTimeout}
                                            onChange={(e) => dirty(setDefaultTimeout)(Number(e.target.value))}
                                            min="1000"
                                            max="120000"
                                            step="1000"
                                        />
                                    </FieldRow>
                                </div>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-exec-retries"
                                title="Retries and parallel runs"
                                description="Retry tests that fail, and run several tests at once."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <CheckRow
                                        id="set-auto-retry"
                                        label="Retry failed tests automatically"
                                        checked={autoRetry}
                                        onChange={dirty(setAutoRetry)}
                                    />
                                    {autoRetry && (
                                        <FieldRow label="Maximum retry attempts" htmlFor="set-max-retries">
                                            <Input
                                                id="set-max-retries"
                                                type="number"
                                                className={inputClass}
                                                value={maxRetries}
                                                onChange={(e) => dirty(setMaxRetries)(Number(e.target.value))}
                                                min="1"
                                                max="5"
                                            />
                                        </FieldRow>
                                    )}
                                    <CheckRow
                                        id="set-parallel"
                                        label="Run tests in parallel"
                                        checked={parallelExecution}
                                        onChange={dirty(setParallelExecution)}
                                    />
                                    {parallelExecution && (
                                        <FieldRow label="Maximum parallel tests" htmlFor="set-max-parallel">
                                            <Input
                                                id="set-max-parallel"
                                                type="number"
                                                className={inputClass}
                                                value={maxParallelTests}
                                                onChange={(e) => dirty(setMaxParallelTests)(Number(e.target.value))}
                                                min="1"
                                                max="10"
                                            />
                                        </FieldRow>
                                    )}
                                </div>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-exec-browsers"
                                title="Several browsers"
                                description="Run each test on every browser you tick below."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <CheckRow
                                        id="set-multi-browser"
                                        label="Run on several browsers"
                                        checked={multiBrowserEnabled}
                                        onChange={dirty(setMultiBrowserEnabled)}
                                    />
                                    <RowGroup title="Browsers">
                                        {BROWSER_OPTIONS.map((b) => (
                                            <CheckRow
                                                key={b.id}
                                                id={`set-browser-${b.id}`}
                                                label={b.name}
                                                hint={b.hint}
                                                checked={selectedBrowsers.includes(b.id)}
                                                onChange={(on) => dirty(setSelectedBrowsers)(toggleIn(selectedBrowsers, b.id, on))}
                                            />
                                        ))}
                                    </RowGroup>
                                </div>
                                <p className={noteClass}>
                                    Each test runs once per selected browser, so the number of test runs multiplies.
                                </p>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-exec-devices"
                                title="Several devices"
                                description="Run each test on every device you tick below."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <CheckRow
                                        id="set-multi-device"
                                        label="Run on several devices"
                                        checked={multiDeviceEnabled}
                                        onChange={dirty(setMultiDeviceEnabled)}
                                    />
                                    <RowGroup title="Devices">
                                        {DEVICE_OPTIONS.map((d) => (
                                            <CheckRow
                                                key={d.id}
                                                id={`set-device-${d.id.replace(/\W+/g, '-')}`}
                                                label={d.name}
                                                hint={d.hint}
                                                checked={selectedDevices.includes(d.id)}
                                                onChange={(on) => dirty(setSelectedDevices)(toggleIn(selectedDevices, d.id, on))}
                                            />
                                        ))}
                                    </RowGroup>
                                </div>
                                <p className={noteClass}>
                                    Each test runs once per selected device, so the number of test runs multiplies.
                                </p>
                            </SettingsCard>
                        </>
                    )}

                    {/* Notifications */}
                    {activeSection === 'notifications' && (
                        <SettingsCard
                            labelledBy="settings-notifications"
                            title="Email notifications"
                            description="Choose whether to get email about test runs, and which events trigger one."
                            footer={saveFooter}
                        >
                            <div className="flex flex-col">
                                <CheckRow
                                    id="set-email-notifications"
                                    label="Send me email about test runs"
                                    checked={emailNotifications}
                                    onChange={dirty(setEmailNotifications)}
                                />
                                {emailNotifications && (
                                    <>
                                        <FieldRow label="Notification email" htmlFor="set-notification-email" wide>
                                            <Input
                                                id="set-notification-email"
                                                type="email"
                                                className={inputClass}
                                                value={notificationEmail}
                                                onChange={(e) => dirty(setNotificationEmail)(e.target.value)}
                                                placeholder="your@email.com"
                                            />
                                        </FieldRow>
                                        <RowGroup title="Email me when">
                                            <CheckRow
                                                id="set-notify-completion"
                                                label="A test run completes"
                                                hint="Any run, pass or fail."
                                                checked={notifyOnCompletion}
                                                onChange={dirty(setNotifyOnCompletion)}
                                            />
                                            <CheckRow
                                                id="set-notify-failure"
                                                label="A test fails"
                                                hint="Only when tests fail."
                                                checked={notifyOnFailure}
                                                onChange={dirty(setNotifyOnFailure)}
                                            />
                                            <CheckRow
                                                id="set-daily-summary"
                                                label="Daily summary"
                                                hint="One report of all test activity each day."
                                                checked={dailySummary}
                                                onChange={dirty(setDailySummary)}
                                            />
                                        </RowGroup>
                                    </>
                                )}
                            </div>
                        </SettingsCard>
                    )}

                    {/* Storage & Retention */}
                    {activeSection === 'storage' && (
                        <>
                            <SettingsCard
                                labelledBy="settings-artifacts"
                                title="Recordings and screenshots"
                                description="What is captured while tests run, to help debug failures."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <FieldRow label="Video recording" htmlFor="set-video-recording" hint="When to record video of a test run.">
                                        <Select value={videoRecording} onValueChange={dirty(setVideoRecording)}>
                                            <SelectTrigger id="set-video-recording" className={selectTriggerClass}>
                                                <SelectValue placeholder="Select recording preference" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="always">Always Record</SelectItem>
                                                <SelectItem value="on-failure">Only on Failure</SelectItem>
                                                <SelectItem value="never">Never Record</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </FieldRow>
                                    <CheckRow
                                        id="set-screenshot-on-error"
                                        label="Screenshot on error"
                                        hint="Capture a screenshot when a test fails."
                                        checked={screenshotOnError}
                                        onChange={dirty(setScreenshotOnError)}
                                    />
                                    <CheckRow
                                        id="set-trace-files"
                                        label="Playwright trace files"
                                        hint="Detailed trace files for debugging."
                                        checked={traceFiles}
                                        onChange={dirty(setTraceFiles)}
                                    />
                                </div>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-retention"
                                title="Retention"
                                description="How long test runs and their files are kept."
                                footer={saveFooter}
                            >
                                <div className="flex flex-col">
                                    <FieldRow label="Keep runs for (days)" htmlFor="set-retention" hint="How long to keep test runs and artifacts.">
                                        <Input
                                            id="set-retention"
                                            type="number"
                                            className={inputClass}
                                            value={retentionPeriod}
                                            onChange={(e) => dirty(setRetentionPeriod)(Number(e.target.value))}
                                            min="1"
                                            max="365"
                                        />
                                    </FieldRow>
                                    <CheckRow
                                        id="set-auto-cleanup"
                                        label="Delete old runs automatically"
                                        checked={autoCleanup}
                                        onChange={dirty(setAutoCleanup)}
                                    />
                                </div>
                            </SettingsCard>
                        </>
                    )}

                    {/* Account Settings */}
                    {activeSection === 'account' && <AccountSection />}
                    {activeSection === 'security' && <MfaSection />}
                    {activeSection === 'instance' && <InstanceSettingsSection />}
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
        <>
            <SettingsCard
                labelledBy="settings-profile"
                title="Profile"
                description="Profile and password changes are not yet supported here. Ask your workspace administrator if you need to update them."
            >
                <dl className="flex flex-col">
                    <div className="flex min-h-11 flex-col gap-1 py-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                        <dt className="text-sm font-medium text-ink">Full name</dt>
                        <dd className="text-sm text-ink-soft sm:text-right">{user?.full_name || '—'}</dd>
                    </div>
                    <div className="flex min-h-11 flex-col gap-1 border-t border-border py-1 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
                        <dt className="text-sm font-medium text-ink">Email</dt>
                        <dd className="break-all text-sm text-ink-soft sm:text-right">{user?.email || '—'}</dd>
                    </div>
                </dl>
            </SettingsCard>

            <ApiKeysSection />
        </>
    );
}

// ---------------------------------------------------------------------------
// API key management
// ---------------------------------------------------------------------------

const inlineCode = 'rounded bg-secondary px-1 font-mono text-xs text-ink';

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

    const statusPill = (key: ApiKeyRead) => {
        const status = keyStatus(key);
        if (status === 'revoked') return <Pill tone="peach">Revoked</Pill>;
        if (status === 'expired') return <Pill tone="neutral">Expired</Pill>;
        return <Pill tone="mint">Active</Pill>;
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
        <>
            <SettingsCard
                labelledBy="settings-api-keys"
                title="API keys"
                description="Workspace keys for programmatic access — CI pipelines, AI agents and the MCP server."
                actions={
                    <Button onClick={() => setCreateOpen(true)} disabled={selectedWorkspaceId === null}>
                        <Plus className="h-4 w-4" />
                        Create API key
                    </Button>
                }
            >
                {workspaces && workspaces.length > 1 && (
                    <FieldRow label="Workspace" htmlFor="api-key-workspace">
                        <Select
                            value={selectedWorkspaceId !== null ? String(selectedWorkspaceId) : undefined}
                            onValueChange={(value) => setSelectedWorkspaceId(Number(value))}
                        >
                            <SelectTrigger id="api-key-workspace" className={selectTriggerClass}>
                                <SelectValue placeholder="Select workspace" />
                            </SelectTrigger>
                            <SelectContent>
                                {workspaces.map((ws) => (
                                    <SelectItem key={ws.id} value={String(ws.id)}>{ws.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </FieldRow>
                )}

                {workspaces && workspaces.length === 0 ? (
                    <p className="text-sm text-ink-soft">
                        You are not a member of any workspace. Join or create a workspace to manage API keys.
                    </p>
                ) : keysLoading ? (
                    <div className="flex items-center gap-2 py-4 text-sm text-ink-soft">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading API keys...
                    </div>
                ) : keys && keys.length > 0 ? (
                    <ul className="overflow-hidden rounded-2xl border border-border">
                        {keys.map((key, i) => (
                            <li
                                key={key.id}
                                className={`flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 ${i > 0 ? 'border-t border-border' : ''}`}
                            >
                                <div className="flex min-w-0 flex-1 flex-col gap-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-sm font-semibold text-ink">{key.name}</span>
                                        <code className={inlineCode}>{key.prefix}…</code>
                                        {statusPill(key)}
                                    </div>
                                    <span className="text-xs text-ink-soft">
                                        Created {formatDate(key.created_at)} · Last used {key.last_used_at ? formatDate(key.last_used_at) : 'Never'}
                                    </span>
                                </div>
                                {keyStatus(key) !== 'revoked' && (
                                    <Button
                                        variant="outline"
                                        size="icon"
                                        className="text-destructive hover:text-destructive"
                                        onClick={() => setRevokeTarget(key)}
                                        disabled={revokeMutation.isPending}
                                        title="Revoke key"
                                        aria-label={`Revoke API key ${key.name}`}
                                    >
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                )}
                            </li>
                        ))}
                    </ul>
                ) : (
                    <p className="py-2 text-sm text-ink-soft">
                        No API keys yet. Create one to let CI pipelines or AI agents call TraceIQ.
                    </p>
                )}
            </SettingsCard>

            <SettingsCard
                labelledBy="settings-mcp"
                title="Connect an IDE through MCP"
                description="Point your IDE's MCP config at the TraceIQ MCP server and authenticate with an API key."
            >
                <p className="text-sm leading-relaxed text-ink-soft">
                    Claude Code uses <code className={inlineCode}>.mcp.json</code>,
                    Cursor <code className={inlineCode}>.cursor/mcp.json</code>, and
                    VS Code <code className={inlineCode}>.vscode/mcp.json</code>.
                </p>
                <pre className="overflow-x-auto rounded-2xl border border-border bg-background p-4 text-xs text-ink">
                    <code>{mcpConfigSnippet}</code>
                </pre>
                <p className="text-xs leading-relaxed text-ink-soft">
                    Replace <code className={inlineCode}>your-traceiq-host</code> with this server's
                    hostname and the <code className={inlineCode}>X-API-Key</code> value with a key
                    created above. The MCP server listens on port 8088.
                </p>
            </SettingsCard>

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
                                <div className={`flex items-start gap-2 ${warnCalloutClass}`}>
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                                    <p>
                                        This is the only time the full key is displayed. Store it somewhere safe —
                                        if you lose it, revoke this key and create a new one.
                                    </p>
                                </div>
                                <div className="flex items-center gap-2">
                                    <code className="flex-1 select-all break-all rounded-xl bg-secondary px-3 py-2 text-sm text-ink">
                                        {createdKey.secret}
                                    </code>
                                    <Button variant="outline" onClick={() => copySecret(createdKey.secret)}>
                                        <Copy className="h-4 w-4" />
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
                                <label htmlFor="api-key-name" className="mb-1.5 block text-sm font-medium text-ink">Key name</label>
                                <Input
                                    id="api-key-name"
                                    className={inputClass}
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
                                            <Loader2 className="h-4 w-4 animate-spin" />
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
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
        </>
    );
}


function RecoveryCodesPanel({ codes, onDismiss }: { codes: string[]; onDismiss: () => void }) {
    return (
        <div className={`flex flex-col gap-3 ${warnCalloutClass}`} role="status">
            <div className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                <p>
                    <span className="font-semibold">Save these recovery codes now.</span> Each works once if you lose your
                    authenticator. They won't be shown again.
                </p>
            </div>
            <div className="grid grid-cols-2 gap-2 font-mono text-sm">
                {codes.map((c) => <div key={c} className="rounded-xl border border-border bg-white px-2 py-1 text-center">{c}</div>)}
            </div>
            <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => { navigator.clipboard?.writeText(codes.join('\n')); toast.success('Recovery codes copied'); }}>
                    <Copy className="h-3.5 w-3.5" /> Copy all
                </Button>
                <Button onClick={onDismiss}>I've saved them</Button>
            </div>
        </div>
    );
}

const codeInputClass = 'h-10 w-32 rounded-full text-center tracking-widest';

function MfaSection() {
    const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
    const [code, setCode] = useState('');
    const [disableCode, setDisableCode] = useState('');
    const [regenCode, setRegenCode] = useState('');
    const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);

    const start = useMutation({
        mutationFn: () => mfaApi.setup(),
        onSuccess: (d) => setSetup(d),
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Could not start MFA setup'),
    });
    const verify = useMutation({
        mutationFn: () => mfaApi.verify(code),
        onSuccess: (d) => { toast.success('MFA enabled — you\'ll be asked for a code at login'); setSetup(null); setCode(''); setRecoveryCodes(d.recovery_codes); },
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Invalid code'),
    });
    const disable = useMutation({
        mutationFn: () => mfaApi.disable(disableCode),
        onSuccess: () => { toast.success('MFA disabled'); setDisableCode(''); setRecoveryCodes(null); },
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Invalid code'),
    });
    const regen = useMutation({
        mutationFn: () => mfaApi.regenerateRecoveryCodes(regenCode),
        onSuccess: (d) => { toast.success('New recovery codes generated'); setRegenCode(''); setRecoveryCodes(d.recovery_codes); },
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Invalid code'),
    });

    return (
        <>
            {recoveryCodes && <RecoveryCodesPanel codes={recoveryCodes} onDismiss={() => setRecoveryCodes(null)} />}

            <SettingsCard
                labelledBy="settings-mfa-enable"
                title="Authenticator app"
                description="Add a second sign-in step using an authenticator app such as Google Authenticator, Authy or 1Password."
            >
                {!setup ? (
                    <Button variant="outline" className="self-start" onClick={() => start.mutate()} disabled={start.isPending}>
                        <KeyRound className="h-4 w-4" /> Set up authenticator
                    </Button>
                ) : (
                    <div className="flex flex-col gap-4">
                        <p className="text-sm text-ink-soft">Scan this QR code with your authenticator app, then enter the current 6-digit code to confirm.</p>
                        <div className="flex flex-col items-start gap-4 sm:flex-row">
                            <div className="shrink-0 rounded-2xl border border-border bg-white p-3">
                                <QRCodeSVG value={setup.otpauth_uri} size={168} level="M" includeMargin={false} />
                            </div>
                            <div className="min-w-0 space-y-2">
                                <p className="text-sm font-medium text-ink">Can't scan? Enter this key manually</p>
                                <div className="break-all rounded-xl border border-border bg-background p-2 font-mono text-sm text-ink">{setup.secret}</div>
                            </div>
                        </div>
                        <div className="flex flex-wrap items-end gap-2 border-t border-border pt-4">
                            <div className="flex flex-col gap-1.5">
                                <label htmlFor="mfa-verify-code" className="text-sm font-medium text-ink">6-digit code</label>
                                <Input id="mfa-verify-code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="123456" className={codeInputClass} />
                            </div>
                            <Button onClick={() => verify.mutate()} disabled={verify.isPending || code.length !== 6}>Verify and turn on</Button>
                        </div>
                    </div>
                )}
            </SettingsCard>

            <SettingsCard
                labelledBy="settings-mfa-recovery"
                title="Recovery codes"
                description="Make a new set of backup codes. The old set stops working. You need a current code from your app."
            >
                <div className="flex flex-wrap items-end gap-2">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="mfa-regen-code" className="text-sm font-medium text-ink">Current code</label>
                        <Input id="mfa-regen-code" inputMode="numeric" autoComplete="one-time-code" value={regenCode} onChange={(e) => setRegenCode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="123456" className={codeInputClass} />
                    </div>
                    <Button variant="outline" onClick={() => regen.mutate()} disabled={regen.isPending || regenCode.length !== 6}>Regenerate codes</Button>
                </div>
            </SettingsCard>

            <SettingsCard
                danger
                labelledBy="settings-mfa-disable"
                title="Turn off two-factor sign-in"
                description="Your account will only need a password to sign in. Enter a current code from your app to confirm."
            >
                <div className="flex flex-wrap items-end gap-2">
                    <div className="flex flex-col gap-1.5">
                        <label htmlFor="mfa-disable-code" className="text-sm font-medium text-ink">Current code</label>
                        <Input id="mfa-disable-code" inputMode="numeric" autoComplete="one-time-code" value={disableCode} onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, '').slice(0, 6))} placeholder="123456" className={codeInputClass} />
                    </div>
                    <Button
                        variant="outline"
                        onClick={() => disable.mutate()}
                        disabled={disable.isPending}
                        className="border-destructive/50 text-destructive hover:bg-destructive/5 hover:text-destructive"
                    >
                        Disable MFA
                    </Button>
                </div>
            </SettingsCard>
        </>
    );
}
