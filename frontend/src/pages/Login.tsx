import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Loader2, ArrowRight, Shield } from "lucide-react";
import MfaEnrollment from "@/components/MfaEnrollment";
import { AuthError, AuthField, AuthLayout } from "@/components/redesign-local/groupF/AuthLayout";
import { authInput, authLink, authOutline, authPrimary } from "@/components/redesign-local/groupF/authStyles";
import { cn } from "@/lib/utils";

export default function Login() {
    const { login } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const [isLoading, setIsLoading] = useState(false);
    // Seed the banner from a redirect (e.g. an expired MFA enrollment session).
    const [error, setError] = useState((location.state as any)?.message || "");
    const [mfaToken, setMfaToken] = useState<string | null>(null);
    const [mfaCode, setMfaCode] = useState("");
    // MFA_REQUIRED policy: login succeeded but the account must enrol an
    // authenticator first; this holds the enrollment challenge token.
    const [mfaSetupToken, setMfaSetupToken] = useState<string | null>(null);
    const [ssoEnabled, setSsoEnabled] = useState(false);
    // One button per configured protocol (OIDC and/or SAML). Older backends
    // return no `providers`, so fall back to the single OIDC entry point.
    const [ssoProviders, setSsoProviders] = useState<{ type: string; label: string; login_path: string }[]>([]);
    // SSO-only mode: hide the password form. ?password=1 keeps a break-glass
    // path for instance admins (the backend exempts them from the policy).
    const [passwordLoginDisabled, setPasswordLoginDisabled] = useState(false);
    // Break-glass password form: require ?password=1 specifically — `.has()`
    // would also match ?password=0.
    const forcePasswordForm = new URLSearchParams(window.location.search).get('password') === '1';
    // Corporate (LDAP) login: tab appears when the instance has it configured.
    const [ldapEnabled, setLdapEnabled] = useState(false);
    const [loginMode, setLoginMode] = useState<'password' | 'ldap'>('password');

    const { register, handleSubmit, formState: { errors } } = useForm();
    const API = API_BASE_URL;

    // SSO: detect the callback (#access_token=…) and finish login; also learn
    // whether SSO is configured so we can show the button.
    useEffect(() => {
        axios.get(`${API}/auth/sso/status`).then((r) => {
            setSsoEnabled(!!r.data?.enabled);
            setPasswordLoginDisabled(!!r.data?.password_login_disabled);
            const providers = Array.isArray(r.data?.providers) ? r.data.providers : [];
            setSsoProviders(providers.length || !r.data?.enabled
                ? providers
                : [{ type: 'oidc', label: 'Sign in with SSO', login_path: '/auth/sso/login' }]);
        }).catch(() => {});
        axios.get(`${API}/auth/ldap/status`).then((r) => setLdapEnabled(!!r.data?.enabled)).catch(() => {});
        const hash = window.location.hash.startsWith('#') ? new URLSearchParams(window.location.hash.slice(1)) : null;
        const ssoToken = hash?.get('access_token');
        const ssoRefresh = hash?.get('refresh_token') || undefined;
        if (ssoToken) {
            window.history.replaceState(null, '', window.location.pathname);
            axios.get(`${API}/auth/me`, { headers: { Authorization: `Bearer ${ssoToken}` } })
                .then((me) => { login(ssoToken, me.data, ssoRefresh); navigate('/'); })
                .catch(() => setError('SSO sign-in failed.'));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const finishLogin = async (accessToken: string, refreshToken?: string) => {
        const userResponse = await axios.get(`${API}/auth/me`, {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        login(accessToken, userResponse.data, refreshToken);
        navigate("/");
    };

    const onSubmit = async (data: any) => {
        setIsLoading(true);
        setError("");
        try {
            // Second step: an MFA challenge is pending — redeem it with the code.
            if (mfaToken) {
                const resp = await axios.post(`${API}/auth/mfa/login`, { mfa_token: mfaToken, code: mfaCode });
                await finishLogin(resp.data.access_token, resp.data.refresh_token);
                return;
            }

            let response;
            if (loginMode === 'ldap') {
                response = await axios.post(`${API}/auth/ldap/login`, {
                    username: data.email, password: data.password,
                });
            } else {
                const formData = new FormData();
                formData.append('username', data.email);
                formData.append('password', data.password);
                response = await axios.post(`${API}/auth/login`, formData);
            }

            if (response.data.mfa_required) {
                setMfaToken(response.data.mfa_token);
                setError("");
                return;  // show the code step
            }
            if (response.data.mfa_setup_required) {
                setMfaSetupToken(response.data.mfa_token);
                setError("");
                return;  // show the enrollment step
            }
            await finishLogin(response.data.access_token, response.data.refresh_token);
        } catch (err: any) {
            console.error("Login failed", err);
            setError(err.response?.data?.detail || (mfaToken ? "Invalid authentication code." : "Invalid credentials."));
        } finally {
            setIsLoading(false);
        }
    };

    const ssoOnly = passwordLoginDisabled && !forcePasswordForm && !mfaSetupToken;
    const title = ssoOnly ? "Sign in" : mfaSetupToken ? undefined : mfaToken ? "Enter your code" : "Welcome back";
    const subtitle = ssoOnly
        ? "This instance uses single sign-on."
        : mfaSetupToken ? undefined
            : mfaToken ? "Two-factor authentication is on for this account."
                : "Sign in to your TraceIQ workspace.";

    return (
        <AuthLayout
            title={title}
            subtitle={subtitle}
            footer={
                <>
                    New here?{" "}
                    <Link to="/signup" className={authLink}>Sign up</Link>
                </>
            }
        >
            {ssoOnly ? (
                <div className="flex flex-col gap-3">
                    {error && <AuthError>{error}</AuthError>}
                    {ssoProviders.map((p) => (
                        <a key={p.type} href={`${API}${p.login_path}`} className={authPrimary}>
                            <Shield className="h-4 w-4" aria-hidden="true" /> {p.label}
                        </a>
                    ))}
                    <a
                        href="?password=1"
                        className="mx-auto mt-1 inline-flex min-h-11 items-center rounded-full px-3 text-[13px] text-ink-soft underline-offset-4 hover:text-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink"
                    >
                        Administrator password sign-in
                    </a>
                </div>
            ) : mfaSetupToken ? (
                <MfaEnrollment
                    token={mfaSetupToken}
                    onExpired={() => {
                        setMfaSetupToken(null);
                        setError("Your enrollment session expired — please sign in again.");
                    }}
                    onCancel={() => {
                        setMfaSetupToken(null);
                        setError("");
                    }}
                    onComplete={async (verify) => {
                        if (verify.access_token) {
                            await finishLogin(verify.access_token, verify.refresh_token);
                        } else {
                            // Shouldn't happen on the policy path; fall back to a fresh login.
                            setMfaSetupToken(null);
                            setError("2FA enabled — sign in again.");
                        }
                    }}
                />
            ) : (
                <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
                    {error && <AuthError>{error}</AuthError>}

                    {!mfaToken && (
                        <>
                            {ldapEnabled && (
                                <div role="group" aria-label="Sign-in method" className="grid grid-cols-2 gap-1 rounded-full bg-background p-1">
                                    {(['password', 'ldap'] as const).map((mode) => (
                                        <button
                                            key={mode} type="button"
                                            onClick={() => setLoginMode(mode)}
                                            aria-pressed={loginMode === mode}
                                            className={cn(
                                                'min-h-11 rounded-full text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink',
                                                loginMode === mode ? 'bg-white text-ink shadow-sm' : 'text-ink-soft hover:text-ink'
                                            )}
                                        >
                                            {mode === 'password' ? 'Email' : 'Corporate (LDAP)'}
                                        </button>
                                    ))}
                                </div>
                            )}
                            <AuthField
                                id="login-email"
                                label={loginMode === 'ldap' ? 'Corporate username' : 'Work email'}
                                error={errors.email?.message as string | undefined}
                            >
                                <input
                                    id="login-email"
                                    {...register("email", { required: "Required" })}
                                    type={loginMode === 'ldap' ? 'text' : 'email'}
                                    autoComplete={loginMode === 'ldap' ? 'username' : 'email'}
                                    autoFocus
                                    aria-invalid={!!errors.email}
                                    aria-describedby={errors.email ? 'login-email-error' : undefined}
                                    className={authInput}
                                    placeholder={loginMode === 'ldap' ? 'jsmith' : 'name@company.com'}
                                />
                            </AuthField>

                            <AuthField
                                id="login-password"
                                label="Password"
                                error={errors.password?.message as string | undefined}
                                aside={loginMode !== 'ldap' && (
                                    <Link to="/forgot-password" className="rounded-sm text-[13px] text-ink-soft underline-offset-4 hover:text-ink hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink">
                                        Forgot password?
                                    </Link>
                                )}
                            >
                                <input
                                    id="login-password"
                                    {...register("password", { required: "Required" })}
                                    type="password"
                                    autoComplete="current-password"
                                    aria-invalid={!!errors.password}
                                    aria-describedby={errors.password ? 'login-password-error' : undefined}
                                    className={authInput}
                                />
                            </AuthField>
                        </>
                    )}

                    {mfaToken && (
                        <AuthField id="login-mfa-code" label="Authentication code" hint="Enter the 6-digit code from your authenticator app.">
                            <input
                                id="login-mfa-code"
                                value={mfaCode}
                                onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                inputMode="numeric"
                                autoComplete="one-time-code"
                                autoFocus
                                placeholder="123456"
                                className={cn(authInput, 'text-center tracking-[0.3em]')}
                            />
                        </AuthField>
                    )}

                    <button type="submit" disabled={isLoading} className={cn(authPrimary, 'mt-1')}>
                        {isLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-label="Signing in" /> : (
                            <>
                                {mfaToken ? 'Verify' : 'Sign in'} <ArrowRight className="h-4 w-4" aria-hidden="true" />
                            </>
                        )}
                    </button>

                    {ssoEnabled && !mfaToken && ssoProviders.length > 0 && (
                        <div className="flex items-center gap-3 text-xs text-ink-soft" aria-hidden="true">
                            <span className="h-px flex-1 bg-border" /> or <span className="h-px flex-1 bg-border" />
                        </div>
                    )}
                    {ssoEnabled && !mfaToken && ssoProviders.map((p) => (
                        <a key={p.type} href={`${API}${p.login_path}`} className={authOutline}>
                            <Shield className="h-4 w-4" aria-hidden="true" /> {p.label}
                        </a>
                    ))}
                </form>
            )}
        </AuthLayout>
    );
}
