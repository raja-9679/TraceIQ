// Named IdP presets. Mirrors backend/app/api/instance_settings.py
// (list_idp_presets / derive_idp_preset) over app/services/idp_catalog.py.
//
// `derive` writes nothing — it returns the instance-settings map the preset
// would produce, and the caller PUTs it to /admin/instance-settings. That keeps
// one write path, and lets the admin see exactly what is about to be saved.
import { api } from '@/lib/api';

export type IdpProtocol = 'oidc' | 'saml' | 'ldap';

export interface IdpPresetField {
    name: string;
    label: string;
    placeholder: string;
    help: string;
    required: boolean;
    secret: boolean;
}

export interface IdpPreset {
    id: string;
    label: string;
    protocol: IdpProtocol;
    fields: IdpPresetField[];
    warnings: string[];
    docs_url: string;
}

export interface DerivedConfig {
    values: Record<string, string>;
    warnings: string[];
}

export const idpCatalogApi = {
    list: async (): Promise<IdpPreset[]> => {
        const response = await api.get('/admin/idp-catalog');
        return response.data;
    },

    derive: async (presetId: string, answers: Record<string, string>): Promise<DerivedConfig> => {
        const response = await api.post(`/admin/idp-catalog/${presetId}/derive`, { answers });
        return response.data;
    },
};

// Axios errors carry the FastAPI `detail` string; fall back when they don't.
export const errorDetail = (err: unknown, fallback: string): string => {
    const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
    return typeof detail === 'string' ? detail : fallback;
};

// Display-only: never render a value we just collected as a secret back in
// clear. Keyed on the setting name because a preset's field names ("client_secret")
// differ from the keys it derives ("OIDC_CLIENT_SECRET").
export const isSecretKey = (key: string): boolean =>
    /(_SECRET|_KEY|PASSWORD)$/.test(key);
