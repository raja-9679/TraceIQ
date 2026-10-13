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
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { AlertTriangle, ArrowLeft, Loader2, Save, Wand2 } from 'lucide-react';
import { toast } from 'sonner';
import { api } from '@/lib/api';
import {
    DerivedConfig, errorDetail, IdpPreset, IdpProtocol, idpCatalogApi, isSecretKey,
} from '@/api/idpCatalog';
import { Pill } from '@/components/redesign/primitives';
import { FieldRow, SettingsCard, inputClass, warnCalloutClass } from '@/components/redesign-local/groupD/settingsForm';

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
        <SettingsCard
            labelledBy="idp-quick-setup-title"
            icon={<Wand2 className="h-4 w-4" aria-hidden="true" />}
            title="Set up a provider"
            description="Pick your identity provider and answer a couple of questions — the issuer URLs, metadata paths, claim names and redirect URLs are filled in for you. Users keep the normal TraceIQ login unless you disable password login, and instance admins always keep it as break-glass."
            footer={selected ? (
                <>
                    <Button
                        disabled={!derived || save.isPending}
                        onClick={() => save.mutate()}
                    >
                        {save.isPending
                            ? <Loader2 className="h-4 w-4 animate-spin" />
                            : <Save className="h-4 w-4" />}
                        Save
                    </Button>
                    <Button
                        variant="outline"
                        disabled={preview.isPending}
                        onClick={() => preview.mutate()}
                    >
                        {preview.isPending
                            ? <Loader2 className="h-4 w-4 animate-spin" />
                            : <Wand2 className="h-4 w-4" />}
                        Preview settings
                    </Button>
                    <span className="basis-full text-xs leading-relaxed text-ink-soft">
                        Preview first, then save. Saving validates the values — for SAML it fetches the IdP metadata,
                        so a wrong URL is reported here rather than at someone's first login.
                    </span>
                </>
            ) : undefined}
        >
            {isLoading && (
                <p className="flex items-center gap-2 text-sm text-ink-soft">
                    <Loader2 className="h-4 w-4 animate-spin" /> Loading providers…
                </p>
            )}

            {!isLoading && !selected && PROTOCOL_ORDER.map((protocol) => {
                const group = (presets || []).filter((p) => p.protocol === protocol);
                if (!group.length) return null;
                return (
                    <div key={protocol} className="flex flex-col gap-2">
                        <h3 className="text-sm font-semibold text-ink">{PROTOCOL_LABELS[protocol]}</h3>
                        <div className="flex flex-wrap gap-2">
                            {group.map((preset) => (
                                <Button
                                    key={preset.id}
                                    variant="outline"
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
                <div className="flex flex-col gap-4">
                    <div className="flex flex-wrap items-center gap-2">
                        <Button variant="ghost" onClick={reset}>
                            <ArrowLeft className="h-4 w-4" /> All providers
                        </Button>
                        <span className="text-sm font-semibold text-ink">{selected.label}</span>
                        <Pill tone="lavender">{PROTOCOL_LABELS[selected.protocol]}</Pill>
                    </div>

                    {selected.warnings.map((warning) => (
                        <div key={warning} className={`flex items-start gap-2 ${warnCalloutClass}`}>
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-ink-soft" aria-hidden="true" />
                            <span>{warning}</span>
                        </div>
                    ))}

                    <div className="flex flex-col divide-y divide-border">
                        {selected.fields.map((field) => (
                            <FieldRow
                                key={field.name}
                                htmlFor={`idp-field-${field.name}`}
                                label={
                                    <>
                                        {field.label}
                                        {!field.required && (
                                            <span className="font-normal text-ink-soft"> (optional)</span>
                                        )}
                                    </>
                                }
                                hint={field.help}
                                wide
                            >
                                <Input
                                    id={`idp-field-${field.name}`}
                                    type={field.secret ? 'password' : 'text'}
                                    autoComplete={field.secret ? 'new-password' : 'off'}
                                    className={inputClass}
                                    placeholder={field.placeholder}
                                    value={answers[field.name] ?? ''}
                                    onChange={(e) => {
                                        setDerived(null);
                                        setAnswers((a) => ({ ...a, [field.name]: e.target.value }));
                                    }}
                                />
                            </FieldRow>
                        ))}
                    </div>

                    {derived && (
                        <div className="overflow-hidden rounded-2xl border border-border">
                            <h3 className="bg-background px-4 py-2 text-sm font-semibold text-ink">
                                These settings will be saved
                            </h3>
                            <dl className="divide-y divide-border">
                                {Object.entries(derived.values).map(([key, value]) => (
                                    <div key={key} className="grid grid-cols-1 gap-1 px-4 py-2 text-xs sm:grid-cols-12 sm:gap-2">
                                        <dt className="sm:col-span-5"><code className="text-ink">{key}</code></dt>
                                        <dd className="break-all text-ink-soft sm:col-span-7">
                                            {isSecretKey(key) ? '••••••••' : value}
                                        </dd>
                                    </div>
                                ))}
                            </dl>
                        </div>
                    )}
                </div>
            )}
        </SettingsCard>
    );
}
