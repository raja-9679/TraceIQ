import { Link } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Loader2, KeyRound, Copy, Trash2, Plus, AlertTriangle } from 'lucide-react';
import { CaptureLevelPointer, ProjectChoicesCard } from '@/components/settings/personalCards';
import { useActiveProjectId } from '@/hooks/useActiveProject';
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

type SectionId = 'general' | 'execution' | 'notifications' | 'account' | 'security';

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
    const activeProjectId = useActiveProjectId();
    const [activeSection, setActiveSection] = useState<SectionId>('general');

    // General Settings
    // UI feature flags — persisted to localStorage immediately (per-browser),
    // independent of the Save button.
    const [aiGeneration, setAiGeneration] = useState(
        () => localStorage.getItem('traceiq.ui.aiGeneration') === 'on'
    );

    // Test Execution Defaults
    const [defaultBrowser, setDefaultBrowser] = useState('chromium');
    const [defaultDevice, setDefaultDevice] = useState('Desktop');

    // Notifications
    const [emailNotifications, setEmailNotifications] = useState(false);
    const [notifyOnCompletion, setNotifyOnCompletion] = useState(true);
    const [notifyOnFailure, setNotifyOnFailure] = useState(true);
    const [notificationEmail, setNotificationEmail] = useState('');

    // Storage & Retention
    const [videoRecording, setVideoRecording] = useState('on-failure');
    const [screenshotOnError, setScreenshotOnError] = useState(true);
    const [traceFiles, setTraceFiles] = useState(true);

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
            setDefaultBrowser(settings.default_browser);
            setDefaultDevice(settings.default_device);
            setMultiBrowserEnabled(settings.multi_browser_enabled);
            setSelectedBrowsers(settings.selected_browsers);
            setMultiDeviceEnabled(settings.multi_device_enabled);
            setSelectedDevices(settings.selected_devices);
            setEmailNotifications(settings.email_notifications);
            setNotifyOnCompletion(settings.notify_on_completion);
            setNotifyOnFailure(settings.notify_on_failure);
            setNotificationEmail(settings.notification_email || '');
            setVideoRecording(settings.video_recording);
            setScreenshotOnError(settings.screenshot_on_error);
            setTraceFiles(settings.trace_files);
        }
    }, [settings]);

    // Each card saves ONLY its own fields (the API is a partial update), so
    // saving the notification card can never overwrite a recording choice.
    const saveMutation = useMutation({
        mutationFn: updateSettings,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['settings'] });
            toast.success('Saved');
        },
        onError: (error: any) => {
            toast.error(error?.response?.data?.detail || 'Failed to save settings');
        }
    });

    const differs = (patch: Record<string, unknown>) => !!settings && Object.entries(patch).some(
        ([k, v]) => JSON.stringify(v ?? null) !== JSON.stringify((settings as unknown as Record<string, unknown>)[k] ?? null));

    const cardFooter = (patch: Record<string, unknown>, reset: () => void) => {
        const changed = differs(patch);
        return (
            <>
                <Button onClick={() => saveMutation.mutate(patch)} disabled={saveMutation.isPending || !changed}>
                    {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    {saveMutation.isPending ? 'Saving…' : 'Save'}
                </Button>
                {changed && (
                    <Button variant="outline" onClick={reset} disabled={saveMutation.isPending}>Discard</Button>
                )}
                <span className="text-xs text-ink-soft" aria-live="polite">{changed ? 'Unsaved changes.' : 'Saved.'}</span>
            </>
        );
    };

    const runDefaultsPatch = { default_browser: defaultBrowser, default_device: defaultDevice };
    const resetRunDefaults = () => { if (settings) { setDefaultBrowser(settings.default_browser); setDefaultDevice(settings.default_device); } };
    const browsersPatch = { multi_browser_enabled: multiBrowserEnabled, selected_browsers: selectedBrowsers };
    const resetBrowsers = () => { if (settings) { setMultiBrowserEnabled(settings.multi_browser_enabled); setSelectedBrowsers(settings.selected_browsers); } };
    const devicesPatch = { multi_device_enabled: multiDeviceEnabled, selected_devices: selectedDevices };
    const resetDevices = () => { if (settings) { setMultiDeviceEnabled(settings.multi_device_enabled); setSelectedDevices(settings.selected_devices); } };
    const notifyPatch = {
        email_notifications: emailNotifications, notification_email: notificationEmail.trim() || null,
        notify_on_completion: notifyOnCompletion, notify_on_failure: notifyOnFailure,
    };
    const resetNotify = () => {
        if (settings) {
            setEmailNotifications(settings.email_notifications); setNotificationEmail(settings.notification_email || '');
            setNotifyOnCompletion(settings.notify_on_completion); setNotifyOnFailure(settings.notify_on_failure);
        }
    };
    const recordingsPatch = { video_recording: videoRecording, screenshot_on_error: screenshotOnError, trace_files: traceFiles };
    const resetRecordings = () => { if (settings) { setVideoRecording(settings.video_recording); setScreenshotOnError(settings.screenshot_on_error); setTraceFiles(settings.trace_files); } };

    // Controls write straight into their card's draft; each card works out its
    // own "unsaved" state by comparing with what the server has.
    const dirty = <T,>(setter: (v: T) => void) => setter;

    const toggleIn = (list: string[], id: string, on: boolean) =>
        on ? [...list, id] : list.filter((x) => x !== id);

    const sections: { id: SectionId; name: string }[] = [
        { id: 'general', name: 'General' },
        { id: 'execution', name: 'Runs and recordings' },
        { id: 'notifications', name: 'Notifications' },
        { id: 'account', name: 'Account and API keys' },
        { id: 'security', name: 'Two-factor sign-in' },
    ];


    return (
        <div className="flex flex-col gap-6">
            <PageHeader title="My settings" subtitle={<>Your own preferences, account and sign-in. Settings shared by a project are in <Link to="/project-settings" className="font-medium text-ink underline underline-offset-2">Project settings</Link>.</>} />

            <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                <aside className="lg:sticky lg:top-6 lg:w-60 lg:flex-none">
                    <SectionNav label="Settings sections" sections={sections} active={activeSection} onChange={setActiveSection} />
                </aside>

                <div className="flex min-w-0 flex-1 flex-col gap-5">
                    {/* General Settings */}
                    {activeSection === 'general' && (
                        <>
                            {/* Theme, timezone and date format used to be offered here, but
                                nothing read them (the app is light-only and shows times in the
                                browser's zone), so the card is hidden until they are wired. */}
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
                                description="Fallback for runs you start yourself, when neither the test nor its suite picks a browser or device. Scheduled runs use the browser set on the schedule."
                                footer={cardFooter(runDefaultsPatch, resetRunDefaults)}
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
                                </div>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-exec-retries"
                                title="Timeouts, retries and parallel runs"
                                description="These belong to the suite and the workers, not to you, so every run of a suite behaves the same whoever starts it."
                            >
                                <ul className="flex list-disc flex-col gap-2 pl-5 text-[13px] leading-relaxed text-ink-soft">
                                    <li>
                                        <strong className="text-ink">Parallel runs:</strong> choose a suite&apos;s execution mode on its page in{' '}
                                        <Link to="/suites" className="font-medium text-ink underline underline-offset-2">Suites</Link>. How many tests run at once depends on how many workers are running.
                                    </li>
                                    <li>
                                        <strong className="text-ink">Automatic retries:</strong> the &quot;Retry failed tests&quot; card on each suite&apos;s page (inherited by its sub-modules).
                                    </li>
                                    <li>
                                        <strong className="text-ink">Step timeout:</strong> set for all workers by the instance admin in{' '}<Link to="/instance-settings" className="font-medium text-ink underline underline-offset-2">Instance settings</Link>.
                                    </li>
                                </ul>
                            </SettingsCard>

                            <SettingsCard
                                labelledBy="settings-exec-browsers"
                                title="Several browsers"
                                description="Run each test on every browser you tick below."
                                footer={cardFooter(browsersPatch, resetBrowsers)}
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
                                footer={cardFooter(devicesPatch, resetDevices)}
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
                        <>
                        <SettingsCard
                            labelledBy="settings-notifications"
                            title="Email notifications"
                            description="Your default for project emails: runs you start, schedules you created, and projects that add you as a recipient. Each project's own settings and your instance admin decide whether email is sent at all."
                            footer={cardFooter(notifyPatch, resetNotify)}
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
                                        <FieldRow label="Notification email" htmlFor="set-notification-email" hint="Leave empty to use your account email." wide>
                                            <Input
                                                id="set-notification-email"
                                                type="email"
                                                className={inputClass}
                                                value={notificationEmail}
                                                onChange={(e) => dirty(setNotificationEmail)(e.target.value)}
                                                placeholder="your@email.com"
                                            />
                                        </FieldRow>
                                        <FieldRow label="Email me about" htmlFor="set-notify-scope" hint="Failed runs only skips runs where every test passed.">
                                            <Select
                                                value={notifyOnCompletion ? 'all' : 'failures'}
                                                onValueChange={(v) => {
                                                    dirty(setNotifyOnCompletion)(v === 'all');
                                                    setNotifyOnFailure(true);
                                                }}
                                            >
                                                <SelectTrigger id="set-notify-scope" className={selectTriggerClass}>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All runs</SelectItem>
                                                    <SelectItem value="failures">Failed runs only</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </FieldRow>
                                    </>
                                )}
                            </div>
                        </SettingsCard>
                        <ProjectChoicesCard />
                        </>
                    )}

                    {/* Storage & Retention */}
                    {activeSection === 'execution' && (
                        <>
                            {/* The project's capture level is the ceiling for the choices
                                below; it is a project setting, so it lives in Project settings. */}
                            <CaptureLevelPointer projectId={activeProjectId} />
                            <SettingsCard
                                labelledBy="settings-artifacts"
                                title="Recordings and screenshots"
                                description="Your choices for runs you start and schedules you created. They apply within each project's capture level (above): video and Playwright traces are only recorded when it is Full."
                                footer={cardFooter(recordingsPatch, resetRecordings)}
                            >
                                <div className="flex flex-col">
                                    <FieldRow label="Video recording" htmlFor="set-video-recording" hint="Which runs keep their video: every run, only failed runs, or none.">
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
                                description="Runs are shared by the whole project, so how long they are kept is a project setting, not a personal one."
                            >
                                <p className="text-[13px] leading-relaxed text-ink-soft">
                                    Set <strong className="text-ink">Retention (days)</strong> in{' '}
                                    <Link to="/project-settings" className="font-medium text-ink underline underline-offset-2">Project settings → Data capture and retention</Link>.
                                    0 there falls back to the instance-wide setting your admin controls; whichever window is shorter wins.
                                    Expired runs are deleted automatically, files included.
                                </p>
                            </SettingsCard>
                        </>
                    )}

                    {/* Account Settings */}
                    {activeSection === 'account' && <AccountSection />}
                    {activeSection === 'security' && <MfaSection />}
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
    const qc = useQueryClient();
    // /auth/me is the only place that reports the caller's own MFA state.
    const me = useQuery({
        queryKey: ['auth-me'],
        queryFn: async () => (await api.get<{ mfa_enabled?: boolean }>('/auth/me')).data,
    });
    const mfaOn = me.data?.mfa_enabled === true;
    const refreshMe = () => qc.invalidateQueries({ queryKey: ['auth-me'] });

    const start = useMutation({
        mutationFn: () => mfaApi.setup(),
        onSuccess: (d) => setSetup(d),
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Could not start MFA setup'),
    });
    const verify = useMutation({
        mutationFn: () => mfaApi.verify(code),
        onSuccess: (d) => { toast.success('MFA enabled — you\'ll be asked for a code at login'); setSetup(null); setCode(''); setRecoveryCodes(d.recovery_codes); refreshMe(); },
        onError: (e: any) => toast.error(e?.response?.data?.detail || 'Invalid code'),
    });
    const disable = useMutation({
        mutationFn: () => mfaApi.disable(disableCode),
        onSuccess: () => { toast.success('MFA disabled'); setDisableCode(''); setRecoveryCodes(null); refreshMe(); },
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
                {me.data && (
                    <p className="flex items-center gap-2 text-sm text-ink-soft">
                        Status: {mfaOn ? <Pill tone="mint">On</Pill> : <Pill tone="neutral">Off</Pill>}
                        {mfaOn && <span>You're asked for a code from your app at every password sign-in.</span>}
                    </p>
                )}
                {mfaOn ? null : !setup ? (
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

            {mfaOn && <>
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
                        disabled={disable.isPending || disableCode.length !== 6}
                        className="border-destructive/50 text-destructive hover:bg-destructive/5 hover:text-destructive"
                    >
                        Disable MFA
                    </Button>
                </div>
            </SettingsCard>
            </>}
        </>
    );
}
