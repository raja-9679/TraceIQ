import { api } from '@/lib/api';

// Mirrors the MFA endpoints in backend/app/api/auth.py

export const mfaApi = {
    setup: async (): Promise<{ secret: string; otpauth_uri: string }> =>
        (await api.post('/auth/mfa/setup')).data,
    verify: async (code: string): Promise<{ mfa_enabled: boolean; recovery_codes: string[] }> =>
        (await api.post('/auth/mfa/verify', { code })).data,
    disable: async (code: string): Promise<{ mfa_enabled: boolean }> =>
        (await api.post('/auth/mfa/disable', { code })).data,
    regenerateRecoveryCodes: async (code: string): Promise<{ recovery_codes: string[] }> =>
        (await api.post('/auth/mfa/recovery-codes', { code })).data,
};
