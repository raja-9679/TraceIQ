import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Loader2, RotateCcw, Save, Send, Sparkles, AlertTriangle, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';

// Mirrors backend/app/api/instance_settings.py::SettingRead
interface InstanceSetting {
    key: string;
    group: string;
    type: 'str' | 'int' | 'bool' | 'list';
    secret: boolean;
    restart_required: boolean;
    label: string;
    description: string;
    source: 'database' | 'environment';
    value: string | number | boolean | string[] | null;
    env_default: string | number | boolean | string[] | null;
    is_set: boolean;
}

const GROUPS: { id: string; title: string; description: string }[] = [
    { id: 'email', title: 'Email (SMTP)', description: 'Outgoing mail for run notifications, password resets and invites.' },
    { id: 'notifications', title: 'Notification channels', description: 'Which channels fire when runs finish.' },
    { id: 'ai', title: 'AI provider', description: 'LLM used for failure analysis and healing. Applies to the backend; execution workers read their own environment.' },
    { id: 'storage', title: 'Storage (S3 / MinIO)', description: 'Where artifacts (traces, videos, screenshots) live.' },
    { id: 'sso', title: 'Single sign-on (OIDC)', description: 'Optional. Password login always stays available, so a bad value here cannot lock you out.' },
    { id: 'policies', title: 'Policies', description: 'Network and retention behavior.' },
];

const fetchInstanceSettings = async (): Promise<InstanceSetting[]> => {
    const response = await api.get('/admin/instance-settings');
    return response.data;
};

export default function InstanceSettingsSection() {
    const queryClient = useQueryClient();
    // Pending edits by key. Secrets hold the newly typed plaintext; '' = untouched.
    const [pending, setPending] = useState<Record<string, string | boolean>>({});
    const [testEmailTo, setTestEmailTo] = useState('');

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
            <Card><CardContent className="p-8 flex items-center gap-2 text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin" /> Loading instance settings…
            </CardContent></Card>
        );
    }

    if (error) {
        const status = (error as any)?.response?.status;
        return (
            <Card><CardContent className="p-8">
                <div className="flex items-center gap-3 text-gray-600">
                    <ShieldAlert className="h-5 w-5 text-amber-500" />
                    {status === 403
                        ? 'Instance settings can only be managed by a tenant admin.'
                        : 'Could not load instance settings.'}
                </div>
            </CardContent></Card>
        );
    }

    const byGroup = (id: string) => (settings || []).filter((s) => s.group === id);
    const hasPending = Object.keys(pending).length > 0;

    const save = () => {
        const values: Record<string, unknown> = {};
        for (const [key, raw] of Object.entries(pending)) {
            const def = settings?.find((s) => s.key === key);
            if (!def) continue;
            if (def.secret && raw === '') continue; // untouched masked field
            if (def.type === 'bool') values[key] = raw === true || raw === 'true';
            else if (def.type === 'int') values[key] = parseInt(String(raw) || '0', 10);
            else if (def.type === 'list')
                values[key] = String(raw).split(',').map((h) => h.trim()).filter(Boolean);
            else values[key] = raw;
        }
        if (Object.keys(values).length === 0) { setPending({}); return; }
        saveMutation.mutate(values);
    };

    const displayValue = (s: InstanceSetting): string => {
        if (s.key in pending) return String(pending[s.key]);
        if (s.type === 'list') return ((s.value as string[]) || []).join(', ');
        return s.value === null || s.value === undefined ? '' : String(s.value);
    };

    const renderField = (s: InstanceSetting) => {
        if (s.type === 'bool') {
            const checked = s.key in pending ? pending[s.key] === true : s.value === true;
            return (
                <Checkbox
                    checked={checked}
                    onCheckedChange={(v) => setPending((p) => ({ ...p, [s.key]: v === true }))}
                />
            );
        }
        if (s.secret) {
            return (
                <Input
                    type="password"
                    autoComplete="new-password"
                    placeholder={s.is_set ? '•••••••• (set — type to replace)' : 'not set'}
                    value={(pending[s.key] as string) ?? ''}
                    onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.value }))}
                />
            );
        }
        return (
            <Input
                type={s.type === 'int' ? 'number' : 'text'}
                placeholder={s.type === 'list' ? 'comma-separated' : ''}
                value={displayValue(s)}
                onChange={(e) => setPending((p) => ({ ...p, [s.key]: e.target.value }))}
            />
        );
    };

    return (
        <div className="space-y-6">
            <Card>
                <CardHeader>
                    <CardTitle>Instance configuration</CardTitle>
                    <CardDescription>
                        Applies to this whole TraceIQ instance. Values saved here are stored in the
                        database (secrets encrypted) and <span className="font-medium">override the
                        environment</span>; use Reset to fall back to the environment value.
                        Core infrastructure (database, Redis, signing keys) stays environment-only by design.
                    </CardDescription>
                </CardHeader>
            </Card>

            {GROUPS.map((group) => {
                const items = byGroup(group.id);
                if (items.length === 0) return null;
                return (
                    <Card key={group.id}>
                        <CardHeader>
                            <CardTitle className="text-lg">{group.title}</CardTitle>
                            <CardDescription>{group.description}</CardDescription>
                            {group.id === 'storage' && (
                                <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2 mt-2">
                                    <AlertTriangle className="h-4 w-4 shrink-0" />
                                    Storage changes apply on the next backend restart, and execution
                                    workers read storage credentials from their own environment.
                                </div>
                            )}
                        </CardHeader>
                        <CardContent className="space-y-4">
                            {items.map((s) => (
                                <div key={s.key} className="grid grid-cols-12 gap-3 items-center">
                                    <div className="col-span-5">
                                        <div className="flex items-center gap-2">
                                            <span className="text-sm font-medium text-gray-700">
                                                {s.label || s.key}
                                            </span>
                                            {s.source === 'database' && (
                                                <Badge variant="secondary" className="text-xs">DB</Badge>
                                            )}
                                            {s.restart_required && (
                                                <Badge variant="outline" className="text-xs">restart</Badge>
                                            )}
                                        </div>
                                        {s.description && (
                                            <p className="text-xs text-gray-500 mt-0.5">{s.description}</p>
                                        )}
                                    </div>
                                    <div className="col-span-5">{renderField(s)}</div>
                                    <div className="col-span-2">
                                        {s.source === 'database' && (
                                            <Button
                                                variant="ghost" size="sm"
                                                title="Reset to environment value"
                                                onClick={() => resetMutation.mutate(s.key)}
                                            >
                                                <RotateCcw className="h-4 w-4 mr-1" /> Reset
                                            </Button>
                                        )}
                                    </div>
                                </div>
                            ))}

                            {group.id === 'email' && (
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <Input
                                        placeholder="you@example.com"
                                        value={testEmailTo}
                                        onChange={(e) => setTestEmailTo(e.target.value)}
                                        className="max-w-xs"
                                    />
                                    <Button
                                        variant="outline" size="sm"
                                        disabled={!testEmailTo || testEmail.isPending}
                                        onClick={() => testEmail.mutate()}
                                    >
                                        {testEmail.isPending
                                            ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                            : <Send className="h-4 w-4 mr-1" />}
                                        Send test email
                                    </Button>
                                    <span className="text-xs text-gray-500">Save first — the test uses saved values.</span>
                                </div>
                            )}
                            {group.id === 'ai' && (
                                <div className="flex items-center gap-2 pt-2 border-t">
                                    <Button
                                        variant="outline" size="sm"
                                        disabled={testLlm.isPending}
                                        onClick={() => testLlm.mutate()}
                                    >
                                        {testLlm.isPending
                                            ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                            : <Sparkles className="h-4 w-4 mr-1" />}
                                        Test LLM connection
                                    </Button>
                                    <span className="text-xs text-gray-500">Save first — the test uses saved values.</span>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                );
            })}

            <div className="flex justify-end">
                <Button onClick={save} disabled={!hasPending || saveMutation.isPending}>
                    {saveMutation.isPending
                        ? <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        : <Save className="h-4 w-4 mr-2" />}
                    Save instance settings
                </Button>
            </div>
        </div>
    );
}
