// Provider picker over the generic SSO/LDAP settings.
//
// The protocol support underneath is already generic (any OIDC issuer, any SAML
// IdP, any LDAP directory). What this adds is the knowledge an admin cannot be
// expected to have: Entra's issuer shape, Keycloak's realm URL, Entra's SAML
// metadata `?appid=`, AD's UPN bind template. Pick a provider, answer two to
// four questions, see exactly which settings that produces, save.
//
// The flat per-key forms below this card stay authoritative for anything a
// preset does not cover, so nothing becomes unreachable.
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { AlertTriangle, ArrowLeft, Loader2, Save, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import {
    DerivedConfig, errorDetail, IdpPreset, IdpProtocol, idpCatalogApi, isSecretKey,
} from '@/api/idpCatalog';

const PROTOCOL_LABELS: Record<IdpProtocol, string> = {
    oidc: 'OpenID Connect',
    saml: 'SAML 2.0',
    ldap: 'LDAP / Active Directory',
};

const PROTOCOL_ORDER: IdpProtocol[] = ['oidc', 'saml', 'ldap'];

export default function IdpQuickSetupCard() {
    const queryClient = useQueryClient();
    const [selected, setSelected] = useState<IdpPreset | null>(null);
    const [answers, setAnswers] = useState<Record<string, string>>({});
    const [derived, setDerived] = useState<DerivedConfig | null>(null);

    const { data: presets, isLoading, error } = useQuery({
        queryKey: ['idp-catalog'],
        queryFn: idpCatalogApi.list,
        retry: false,
    });

    const reset = () => {
        setSelected(null);
        setAnswers({});
        setDerived(null);
    };

    const preview = useMutation({
        mutationFn: () => idpCatalogApi.derive(selected!.id, answers),
        onSuccess: setDerived,
        onError: (err) =>
            toast.error(errorDetail(err, 'Could not build the configuration')),
    });

    // Deliberately the same endpoint the flat forms use — one write path, one
    // set of validations (which includes fetching SAML metadata at save time).
    const save = useMutation({
        mutationFn: async () => api.put('/admin/instance-settings', { values: derived!.values }),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['instance-settings'] });
            toast.success(`${selected!.label} saved. Sign-in options update within ~15 seconds.`);
            reset();
        },
        onError: (err) =>
            toast.error(errorDetail(err, 'Failed to save the configuration')),
    });

    // A 403 here means the same thing as on the settings list, which already
    // says so — stay quiet rather than showing the message twice.
    if (error || (!isLoading && !presets?.length)) return null;

    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-lg flex items-center gap-2">
                    <Wand2 className="h-4 w-4" /> Set up a provider
                </CardTitle>
                <CardDescription>
                    Pick your identity provider and answer a couple of questions — the
                    issuer URLs, metadata paths, claim names and redirect URLs are filled
                    in for you. Users keep the normal TraceIQ login unless you disable
                    password login, and instance admins always keep it as break-glass.
                </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
                {isLoading && (
                    <div className="flex items-center gap-2 text-sm text-gray-500">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading providers…
                    </div>
                )}

                {!isLoading && !selected && PROTOCOL_ORDER.map((protocol) => {
                    const group = (presets || []).filter((p) => p.protocol === protocol);
                    if (!group.length) return null;
                    return (
                        <div key={protocol}>
                            <p className="text-xs font-medium uppercase tracking-wide text-gray-500 mb-2">
                                {PROTOCOL_LABELS[protocol]}
                            </p>
                            <div className="flex flex-wrap gap-2">
                                {group.map((preset) => (
                                    <Button
                                        key={preset.id}
                                        variant="outline"
                                        size="sm"
                                        onClick={() => { setSelected(preset); setAnswers({}); setDerived(null); }}
                                    >
                                        {preset.label}
                                    </Button>
                                ))}
                            </div>
                        </div>
                    );
                })}

                {selected && (
                    <div className="space-y-4">
                        <div className="flex items-center gap-2">
                            <Button variant="ghost" size="sm" onClick={reset}>
                                <ArrowLeft className="h-4 w-4 mr-1" /> All providers
                            </Button>
                            <span className="text-sm font-medium text-gray-700">{selected.label}</span>
                            <Badge variant="secondary" className="text-xs">
                                {PROTOCOL_LABELS[selected.protocol]}
                            </Badge>
                        </div>

                        {selected.warnings.map((warning) => (
                            <div
                                key={warning}
                                className="flex items-start gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-3 py-2"
                            >
                                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                                <span>{warning}</span>
                            </div>
                        ))}

                        <div className="space-y-3">
                            {selected.fields.map((field) => (
                                <div key={field.name} className="grid grid-cols-12 gap-3 items-center">
                                    <div className="col-span-5">
                                        <Label className="text-sm font-medium text-gray-700">
                                            {field.label}
                                            {!field.required && (
                                                <span className="text-gray-400 font-normal"> (optional)</span>
                                            )}
                                        </Label>
                                        {field.help && (
                                            <p className="text-xs text-gray-500 mt-0.5">{field.help}</p>
                                        )}
                                    </div>
                                    <div className="col-span-7">
                                        <Input
                                            type={field.secret ? 'password' : 'text'}
                                            autoComplete={field.secret ? 'new-password' : 'off'}
                                            placeholder={field.placeholder}
                                            value={answers[field.name] ?? ''}
                                            onChange={(e) => {
                                                setDerived(null);
                                                setAnswers((a) => ({ ...a, [field.name]: e.target.value }));
                                            }}
                                        />
                                    </div>
                                </div>
                            ))}
                        </div>

                        {derived && (
                            <div className="border rounded-md divide-y">
                                <div className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-gray-500 bg-gray-50">
                                    These settings will be saved
                                </div>
                                {Object.entries(derived.values).map(([key, value]) => (
                                    <div key={key} className="px-3 py-2 grid grid-cols-12 gap-2 text-xs">
                                        <code className="col-span-5 text-gray-700">{key}</code>
                                        <span className="col-span-7 text-gray-600 break-all">
                                            {isSecretKey(key) ? '••••••••' : value}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}

                        <div className="flex items-center gap-2">
                            <Button
                                variant="outline"
                                size="sm"
                                disabled={preview.isPending}
                                onClick={() => preview.mutate()}
                            >
                                {preview.isPending
                                    ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                    : <Wand2 className="h-4 w-4 mr-1" />}
                                Preview settings
                            </Button>
                            <Button
                                size="sm"
                                disabled={!derived || save.isPending}
                                onClick={() => save.mutate()}
                            >
                                {save.isPending
                                    ? <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                                    : <Save className="h-4 w-4 mr-1" />}
                                Save
                            </Button>
                            <span className="text-xs text-gray-500">
                                Saving validates the values — for SAML it fetches the IdP metadata,
                                so a wrong URL is reported here rather than at someone's first login.
                            </span>
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
