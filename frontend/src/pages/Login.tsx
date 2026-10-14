import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, AlertTriangle, Shield, Globe, Network, Sparkles, ShieldCheck } from "lucide-react";
import MfaEnrollment from "@/components/MfaEnrollment";
import SweepShell from "@/components/auth-sweep/SweepShell";

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

    return (
        <SweepShell
            title={<>Continuous verification for every journey, API and release.</>}
            subtitle="TraceIQ exercises your web and mobile journeys, APIs and integrations on every change — and tells your team exactly what broke and why."
            points={[
                { icon: Globe, title: "UI & mobile journeys", desc: "Every browser and device, with visual regression on each release." },
                { icon: Network, title: "APIs & integrations", desc: "REST, GraphQL, OAuth, TLS and third-party services, checked in-flow." },
                { icon: Sparkles, title: "AI failure analysis", desc: "Root cause on every failure; drifting selectors heal for review." },
                { icon: ShieldCheck, title: "Enterprise controls", desc: "SSO, SCIM, role-based access, redaction and a tamper-evident audit trail." },
            ]}
            trust={["SAML & OIDC single sign-on", "SCIM provisioning", "Tamper-evident audit trail", "Self-hosted or cloud"]}
            topLink={<>New to TraceIQ? <Link to="/signup" className="font-medium text-sky-700 hover:text-sky-900">Create an account</Link></>}
        >
            <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                className="w-full"
            >
                <div className="mb-8">
                    <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">
                        {mfaToken ? "Two-step verification" : "Sign in to TraceIQ"}
                    </h1>
                    <p className="mt-1.5 text-sm text-zinc-500">
                        {mfaToken ? "Enter the code from your authenticator app." : "Use your work account to continue."}
                    </p>
                </div>

                <div>
                    {passwordLoginDisabled && !forcePasswordForm && !mfaSetupToken ? (
                        <div className="space-y-4">
                            <p className="text-sm text-zinc-600">
                                This instance uses single sign-on.
                            </p>
                            {ssoProviders.map((p) => (
                                <a
                                    key={p.type}
                                    href={`${API}${p.login_path}`}
                                    className="w-full h-10 px-4 bg-[#0B1220] hover:bg-[#16213A] text-white text-sm font-medium rounded-md transition-colors flex items-center justify-center gap-2"
                                >
                                    <Shield className="w-4 h-4" /> {p.label}
                                </a>
                            ))}
                            <a href="?password=1"
                               className="block text-center text-xs text-zinc-500 hover:text-zinc-800 transition-colors">
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
                    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
                        <AnimatePresence>
                            {error && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0, overflow: 'hidden' }}
                                    animate={{ opacity: 1, height: "auto" }}
                                    exit={{ opacity: 0, height: 0 }}
                                    className="p-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-md flex items-center gap-2"
                                >
                                    <AlertTriangle className="w-4 h-4 shrink-0" />
                                    {error}
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {!mfaToken && (
                            <>
                                {ldapEnabled && (
                                    <div className="grid grid-cols-2 gap-1 p-1 bg-zinc-100 rounded-md">
                                        {(['password', 'ldap'] as const).map((mode) => (
                                            <button
                                                key={mode} type="button"
                                                onClick={() => setLoginMode(mode)}
                                                className={`py-1.5 text-xs font-medium rounded transition-colors ${loginMode === mode ? 'bg-white text-zinc-900 shadow-sm' : 'text-zinc-500 hover:text-zinc-700'}`}
                                            >
                                                {mode === 'password' ? 'Email' : 'Corporate (LDAP)'}
                                            </button>
                                        ))}
                                    </div>
                                )}
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium text-zinc-700">
                                        {loginMode === 'ldap' ? 'Corporate username' : 'Work email'}
                                    </label>
                                    <input
                                        {...register("email", { required: "Required" })}
                                        type={loginMode === 'ldap' ? 'text' : 'email'}
                                        autoFocus
                                        className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                        placeholder={loginMode === 'ldap' ? 'jsmith' : 'name@company.com'}
                                    />
                                    {errors.email && <span className="text-xs text-red-500">{errors.email.message as string}</span>}
                                </div>

                                <div className="space-y-1.5">
                                    <div className="flex justify-between items-center">
                                        <label className="text-sm font-medium text-zinc-700">Password</label>
                                        {loginMode !== 'ldap' && (
                                        <Link to="/forgot-password" className="text-sm font-medium text-sky-700 hover:text-sky-900 transition-colors">Forgot password?</Link>
                                        )}
                                    </div>
                                    <input
                                        {...register("password", { required: "Required" })}
                                        type="password"
                                        className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                    />
                                    {errors.password && <span className="text-xs text-red-500">{errors.password.message as string}</span>}
                                </div>
                            </>
                        )}

                        {mfaToken && (
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-zinc-700">Authentication code</label>
                                <input
                                    value={mfaCode}
                                    onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                    inputMode="numeric"
                                    autoFocus
                                    placeholder="123456"
                                    className="w-full h-11 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-lg font-mono tracking-[0.4em] text-center focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                />
                                <p className="text-xs text-zinc-500">The code refreshes every 30 seconds.</p>
                            </div>
                        )}

                        <button
                            type="submit"
                            disabled={isLoading}
                            className="w-full h-10 px-4 bg-[#0B1220] hover:bg-[#16213A] text-white text-sm font-medium rounded-md transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center"
                        >
                            {isLoading ? <span className="flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Verifying…</span> : (
                                <span>{mfaToken ? 'Verify' : 'Sign in'}</span>
                            )}
                        </button>

                        {ssoEnabled && !mfaToken && ssoProviders.length > 0 && (
                            <div className="flex items-center gap-3 text-xs text-zinc-400">
                                <span className="h-px flex-1 bg-zinc-200" /> or <span className="h-px flex-1 bg-zinc-200" />
                            </div>
                        )}
                        {ssoEnabled && !mfaToken && ssoProviders.map((p) => (
                            <a
                                key={p.type}
                                href={`${API}${p.login_path}`}
                                className="w-full h-10 px-4 border border-zinc-300 hover:bg-zinc-50 text-zinc-800 text-sm font-medium rounded-md transition-colors flex items-center justify-center gap-2"
                            >
                                <Shield className="w-4 h-4" /> {p.label}
                            </a>
                        ))}
                    </form>
                    )}
                </div>
            </motion.div>
        </SweepShell>
    );
}
