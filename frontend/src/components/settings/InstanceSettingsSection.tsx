import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Loader2, RotateCcw, Send, Sparkles, AlertTriangle, ShieldAlert, Inbox } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import LLMProvidersCard from '@/components/settings/LLMProvidersCard';
import IdpQuickSetupCard from '@/components/settings/IdpQuickSetupCard';
import SiemStatusPanel from '@/components/settings/SiemStatusPanel';
import CostPricesEditor from '@/components/settings/CostPricesEditor';
import { Pill } from '@/components/redesign/primitives';
import {
    FieldRow, RowGroup, SettingsCard, checkboxClass, inputClass, warnCalloutClass,
} from '@/components/redesign-local/groupD/settingsForm';

// Mirrors backend/app/api/instance_settings.py::SettingRead
interface InstanceSetting {
    key: string;
    group: string;
    type: 'str' | 'int' | 'bool' | 'list' | 'json';
    secret: boolean;
    restart_required: boolean;
    label: string;
    description: string;
    source: 'database' | 'environment';
    value: string | number | boolean | string[] | Record<string, unknown> | null;
    env_default: string | number | boolean | string[] | Record<string, unknown> | null;
    is_set: boolean;
}

const GROUPS: { id: string; title: string; description: string }[] = [
    { id: 'email', title: 'Email (SMTP)', description: 'Outgoing mail for run notifications, password resets and invites.' },
    { id: 'notifications', title: 'Notification channels', description: 'Which channels fire when runs finish.' },
    { id: 'ai', title: 'AI provider (legacy single-provider fallback)', description: 'Used only when NO saved providers exist above. Applies to the backend; execution workers read their own environment.' },
    { id: 'storage', title: 'Storage (S3 / MinIO)', description: 'Where artifacts (traces, videos, screenshots) live.' },
    { id: 'sso', title: 'Single sign-on (OIDC)', description: 'Optional. Works with Entra ID, Okta, Google Workspace, Keycloak — any OIDC IdP. Instance admins always keep password login as break-glass, so a bad value here cannot lock you out.' },
    { id: 'saml', title: 'Single sign-on (SAML 2.0)', description: 'Optional, for SAML-first IdPs (Entra ID enterprise apps, Okta, ADFS, PingFederate, Shibboleth). Set the assertion consumer URL to https://<your-traceiq>/api/auth/saml/acs and paste the IdP metadata URL; give the IdP our metadata from /api/auth/saml/metadata. Settings are validated (and the metadata fetched) when you save. See docs/ENTERPRISE_AUTH.md.' },
    { id: 'ldap', title: 'LDAP / Active Directory', description: 'Optional corporate login for on-prem AD. TraceIQ binds as the logging-in user — no service account, no password sync. See docs/ENTERPRISE_AUTH.md.' },
    { id: 'federation', title: 'Federated provisioning (SSO + LDAP)', description: 'Where users who sign in through your IdP land. The default gives every one of them their own tenant, which is right for a single user and wrong for an organisation — set a target workspace before rolling SSO out to a team. See docs/ENTERPRISE_AUTH.md.' },
    { id: 'test_inbox', title: 'Test inbox & webhook capture', description: 'Lets test cases receive email ({{inbox.address}} + Wait For Email) and webhooks ({{webhook.url}} + Wait For Webhook). Mailpit: start the compose profile "inbox" and point the app under test\'s SMTP at mailpit:1025. IMAP: a real mailbox that supports plus-addressing (qa+tag@company.com). Mail credentials stay on the backend — workers never see them.' },
    { id: 'siem', title: 'SIEM streaming', description: 'Stream every audit event (with its hash-chain fields, so your SIEM can verify the chain) and every security alert to syslog, Splunk HEC, Elastic or a signed webhook. Delivery is asynchronous with retry; a SIEM outage never blocks a request.' },
    { id: 'security_alerts', title: 'Security alerts', description: 'Rules over the audit stream — failed-login bursts, admin grants, API key changes, MFA or SSO-only being weakened, SCIM token changes and SCIM deactivations. Alerts go to the channels below and to the SIEM.' },
    { id: 'policies', title: 'Policies', description: 'Network and retention behavior.' },
    { id: 'worker', title: 'Execution workers', description: 'Runtime config every execution worker fetches from /api/internal/worker-config at start and every 60s (blank = the worker keeps its own environment value), plus the instance-wide egress allowlist. Redis, the worker secret, MinIO credentials and WORKER_REGION stay environment-only on the worker.' },
    { id: 'network', title: 'Network access control', description: 'Trusted reverse proxies (whose X-Forwarded-For is believed), the SCIM source allowlist, and optional mTLS for worker → backend calls. A wrong CIDR here can lock out your IdP — values are validated on save.' },
    { id: 'observability', title: 'Observability', description: 'Prometheus the backend queries to show server metrics next to k6 load-test results, and the Jaeger or Tempo it reads backend traces from for projects with trace correlation on (Project settings → Backend traces).' },
    { id: 'cost', title: 'Cost', description: 'Your own prices, used to turn AI tokens and worker time into money on the Costs page and for project budgets. Nothing is priced until a currency is set; a model missing from the table shows as not priced. TraceIQ ships no vendor prices.' },
];

// Subject DNs, region grants ("eu=12,15") and PromQL templates contain
// commas, so these lists are edited one entry per line.
const LINE_LIST_KEYS = new Set([
    'WORKER_MTLS_ALLOWED_SUBJECTS', 'WORKER_REGIONS', 'PROMETHEUS_QUERY_TEMPLATES',
]);

const fetchInstanceSettings = async (): Promise<InstanceSetting[]> => {
    const response = await api.get('/admin/instance-settings');
    return response.data;
};

export default function InstanceSettingsSection() {
    const queryClient = useQueryClient();
    // Pending edits by key. Secrets hold the newly typed plaintext; '' = untouched.
    const [pending, setPending] = useState<Record<string, string | boolean>>({});
    const [testEmailTo, setTestEmailTo] = useState('');
    // Structured editors (json settings) that currently hold an invalid value.
    const [jsonErrors, setJsonErrors] = useState<Record<string, string>>({});

    const { data: settings, isLoading, error } = useQuery({
        queryKey: ['instance-settings'],
        queryFn: fetchInstanceSettings,
        retry: false,
    });

    const saveMutation = useMutation({
        mutationFn: async (values: Record<string, unknown>) => {
            const response = await api.put('/admin/instance-settings', { values });
            return response.data;
        },
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['instance-settings'] });
            setPending({});
            setJsonErrors({});
            toast.success('Instance settings saved. Changes apply within ~15 seconds (storage needs a restart).');
        },
        onError: (err: any) =>
            toast.error(err.response?.data?.detail || 'Failed to save instance settings'),
    });

    const resetMutation = useMutation({
        mutationFn: async (key: string) => api.delete(`/admin/instance-settings/${key}`),
        onSuccess: (_data, key) => {
            queryClient.invalidateQueries({ queryKey: ['instance-settings'] });
            setPending((p) => { const { [key]: _drop, ...rest } = p; return rest; });
            toast.success(`${key} reset to the environment value`);
        },
        onError: (err: any) =>
            toast.error(err.response?.data?.detail || 'Failed to reset setting'),
    });

    const testEmail = useMutation({
        mutationFn: async () => api.post('/admin/instance-settings/test-email', { to: testEmailTo }),
        onSuccess: () => toast.success(`Test email sent to ${testEmailTo}`),
        onError: (err: any) => toast.error(err.response?.data?.detail || 'SMTP test failed'),
    });

    const testInbox = useMutation({
        mutationFn: async () => {
            const response = await api.post('/admin/instance-settings/test-inbox');
            return response.data;
        },
        onSuccess: (data: { detail: string; example_address: string }) =>
            toast.success(`${data.detail}. Addresses look like ${data.example_address}`),
        onError: (err: { response?: { data?: { detail?: string } } }) =>
            toast.error(err.response?.data?.detail || 'Inbox test failed'),
    });

    const testLlm = useMutation({
        mutationFn: async () => {
            const response = await api.post('/admin/instance-settings/test-llm');
            return response.data;
        },
        onSuccess: (data: any) => toast.success(`LLM ok (${data.provider}): "${data.reply}"`),
        onError: (err: any) => toast.error(err.response?.data?.detail || 'LLM test failed'),
    });

    if (isLoading) {
        return (
            <SettingsCard title="Instance configuration">
                <p className="flex items-center gap-2 text-sm text-ink-soft">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading instance settings…
                </p>
            </SettingsCard>
        );
    }

    if (error) {
        const status = (error as any)?.response?.status;
        return (
            <SettingsCard title="Instance configuration">
                <p className={`flex items-center gap-3 ${warnCalloutClass}`} role="status">
                    <ShieldAlert className="h-5 w-5 shrink-0 text-ink-soft" aria-hidden="true" />
                    {status === 403
                        ? 'Instance settings can only be managed by a tenant admin.'
                        : 'Could not load instance settings.'}
                </p>
            </SettingsCard>
        );
    }

    const byGroup = (id: string) => (settings || []).filter((s) => s.group === id);
    const hasPending = Object.keys(pending).length > 0;

    const save = () => {
        const firstJsonError = Object.entries(jsonErrors)[0];
        if (firstJsonError) { toast.error(`${firstJsonError[0]}: ${firstJsonError[1]}`); return; }
        const values: Record<string, unknown> = {};
        for (const [key, raw] of Object.entries(pending)) {
            const def = settings?.find((s) => s.key === key);
            if (!def) continue;
            if (def.secret && raw === '') continue; // untouched masked field
            if (def.type === 'bool') values[key] = raw === true || raw === 'true';
            else if (def.type === 'int') {
                // A cleared int field must not be sent — sending 0 would silently
                // override the environment fallback with a real value. Skip empty
                // and non-numeric input rather than coercing to 0 / NaN→null.
                const s = String(raw).trim();
                if (s === '') continue;
                const n = parseInt(s, 10);
                if (Number.isNaN(n)) {
                    toast.error(`${def.label || key} must be a number`);
                    continue;
                }
                values[key] = n;
            }
            // json: '' resets to unset (the server stores nothing).
            else if (def.type === 'json') values[key] = String(raw).trim() === '' ? null : JSON.parse(String(raw));
            else if (def.type === 'list')
                values[key] = String(raw)
                    .split(LINE_LIST_KEYS.has(key) ? '\n' : ',')
                    .map((h) => h.trim()).filter(Boolean);
            else values[key] = raw;
        }
        if (Object.keys(values).length === 0) { setPending({}); return; }
        saveMutation.mutate(values);
    };

    const displayValue = (s: InstanceSetting): string => {
        if (s.key in pending) return String(pending[s.key]);
        if (s.type === 'list') return ((s.value as string[]) || []).join(LINE_LIST_KEYS.has(s.key) ? '\n' : ', ');
        return s.value === null || s.value === undefined ? '' : String(s.value);
    };

    const fieldId = (key: string) => `inst-${key}`;

    const renderField = (s: InstanceSetting) => {
        if (s.type === 'json') {
            return (
                <CostPricesEditor
                    key={`${s.key}-${JSON.stringify(s.value)}`}
                    value={s.value}
                    onChange={(json, err) => {
                        setPending((p) => ({ ...p, [s.key]: json }));
                        setJsonErrors((e) => { const rest = { ...e }; delete rest[s.key]; return err ? { ...rest, [s.key]: err } : rest; });
                    }}
                />
            );
        }
        if (s.type === 'bool') {
            const checked = s.key in pending ? pending[s.key] === true : s.value === true;
            return (
                <input
                    id={fieldId(s.key)}
                    type="checkbox"
                    className={checkboxClass}
                    checked={checked}
                    onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.checked }))}
                />
            );
        }
        if (s.secret) {
            return (
                <Input
                    id={fieldId(s.key)}
                    type="password"
                    autoComplete="new-password"
                    className={inputClass}
                    placeholder={s.is_set ? '•••••••• (set — type to replace)' : 'not set'}
                    value={(pending[s.key] as string) ?? ''}
                    onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.value }))}
                />
            );
        }
        if (LINE_LIST_KEYS.has(s.key)) {
            return (
                <textarea
                    aria-label={s.label || s.key}
                    className="w-full min-h-20 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono"
                    placeholder="one entry per line"
                    value={displayValue(s)}
                    onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.value }))}
                />
            );
        }
        return (
            <Input
                id={fieldId(s.key)}
                type={s.type === 'int' ? 'number' : 'text'}
                className={inputClass}
                placeholder={s.type === 'list' ? 'comma-separated' : ''}
                value={displayValue(s)}
                onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.value }))}
            />
        );
    };

    const pendingCount = Object.keys(pending).length;

    // One save path for every group (unchanged): each card's button sends all
    // pending instance edits, as the single button at the bottom used to.
    const saveFooter = (
        <>
            <Button onClick={save} disabled={!hasPending || saveMutation.isPending}>
                {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                Save instance settings
            </Button>
            <span className="text-xs text-ink-soft" aria-live="polite">
                {hasPending
                    ? `${pendingCount} unsaved change${pendingCount === 1 ? '' : 's'} across instance settings.`
                    : 'No unsaved changes.'}
            </span>
        </>
    );

    return (
        <>
            <SettingsCard
                title="Instance configuration"
                description={
                    <>
                        Applies to this whole TraceIQ instance. Values saved here are stored in the
                        database (secrets encrypted) and <span className="font-medium text-ink">override the
                        environment</span>; use Reset to fall back to the environment value.
                        Core infrastructure (database, Redis, signing keys) stays environment-only by design.
                    </>
                }
            />

            {GROUPS.map((group) => {
                const items = byGroup(group.id);
                if (items.length === 0) return null;
                return (
                    <div key={group.id} className="flex flex-col gap-5">
                    {group.id === 'ai' && <LLMProvidersCard />}
                    {group.id === 'sso' && <IdpQuickSetupCard />}
                    <SettingsCard
                        labelledBy={`inst-group-${group.id}`}
                        title={group.title}
                        description={group.description}
                        footer={saveFooter}
                    >
                        {group.id === 'storage' && (
                            <div className={`flex items-start gap-2 ${warnCalloutClass}`}>
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                                <span>
                                    Storage changes apply on the next backend restart, and execution
                                    workers read storage credentials from their own environment.
                                </span>
                            </div>
                        )}
                        <div className="flex flex-col divide-y divide-border">
                            {items.map((s) => (
                                <FieldRow
                                    key={s.key}
                                    label={s.label || s.key}
                                    htmlFor={fieldId(s.key)}
                                    hint={s.description}
                                    wide={s.type !== 'bool'}
                                    badges={
                                        <>
                                            {s.source === 'database' && <Pill tone="lavender">Saved here</Pill>}
                                            {s.restart_required && <Pill tone="butter">Needs restart</Pill>}
                                        </>
                                    }
                                >
                                    <div className={s.type === 'bool' ? 'flex min-h-10 flex-1 items-center sm:justify-end' : 'min-w-0 flex-1'}>
                                        {renderField(s)}
                                    </div>
                                    {s.source === 'database' && (
                                        <Button
                                            variant="outline"
                                            className="flex-none"
                                            title="Reset to environment value"
                                            aria-label={`Reset ${s.label || s.key} to the environment value`}
                                            onClick={() => resetMutation.mutate(s.key)}
                                        >
                                            <RotateCcw className="h-4 w-4" /> Reset
                                        </Button>
                                    )}
                                </FieldRow>
                            ))}
                        </div>

                        {group.id === 'email' && (
                            <RowGroup title="Check the mail settings">
                                <div className="flex flex-wrap items-end gap-2">
                                    <div className="flex min-w-0 flex-col gap-1.5">
                                        <label htmlFor="inst-test-email-to" className="text-sm font-medium text-ink">Send a test email to</label>
                                        <Input
                                            id="inst-test-email-to"
                                            placeholder="you@example.com"
                                            value={testEmailTo}
                                            onChange={(e) => setTestEmailTo(e.target.value)}
                                            className={`${inputClass} w-72 max-w-full`}
                                        />
                                    </div>
                                    <Button
                                        variant="outline"
                                        disabled={!testEmailTo || testEmail.isPending}
                                        onClick={() => testEmail.mutate()}
                                    >
                                        {testEmail.isPending
                                            ? <Loader2 className="h-4 w-4 animate-spin" />
                                            : <Send className="h-4 w-4" />}
                                        Send test email
                                    </Button>
                                </div>
                                <p className="mt-2 text-xs text-ink-soft">Save first — the test uses saved values.</p>
                            </RowGroup>
                        )}
                        {group.id === 'test_inbox' && (
                            <RowGroup title="Check the inbox connection">
                                <div className="flex flex-wrap items-center gap-3">
                                    <Button
                                        variant="outline"
                                        disabled={testInbox.isPending}
                                        onClick={() => testInbox.mutate()}
                                    >
                                        {testInbox.isPending
                                            ? <Loader2 className="h-4 w-4 animate-spin" />
                                            : <Inbox className="h-4 w-4" />}
                                        Test inbox connection
                                    </Button>
                                    <span className="text-xs text-ink-soft">Save first — the test uses saved values.</span>
                                </div>
                            </RowGroup>
                        )}
                        {group.id === 'siem' && (
                            <RowGroup title="Delivery status">
                                <SiemStatusPanel />
                            </RowGroup>
                        )}
                        {group.id === 'ai' && (
                            <RowGroup title="Check the AI connection">
                                <div className="flex flex-wrap items-center gap-3">
                                    <Button
                                        variant="outline"
                                        disabled={testLlm.isPending}
                                        onClick={() => testLlm.mutate()}
                                    >
                                        {testLlm.isPending
                                            ? <Loader2 className="h-4 w-4 animate-spin" />
                                            : <Sparkles className="h-4 w-4" />}
                                        Test LLM connection
                                    </Button>
                                    <span className="text-xs text-ink-soft">Save first — the test uses saved values.</span>
                                </div>
                            </RowGroup>
                        )}
                    </SettingsCard>
                    </div>
                );
            })}
        </>
    );
}
