import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, ArrowRight, LayoutGrid, AlertTriangle, Shield } from "lucide-react";
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
            eyebrow="Continuous verification"
            title={<>Every journey, every API,<br />every release — checked.</>}
            subtitle="TraceIQ walks your UI, APIs, data stores and third-party integrations on every change, and tells you exactly what broke and why."
        >
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.4 }}
                className="w-full max-w-[400px]"
            >
                <div className="flex flex-col items-center mb-8">
                    <div className="w-14 h-14 bg-zinc-900 rounded-2xl flex items-center justify-center mb-6 shadow-2xl shadow-zinc-300/50 relative overflow-hidden group">
                        <LayoutGrid className="w-7 h-7 text-white relative z-10" />
                        <div className="absolute inset-0 bg-gradient-to-tr from-indigo-500/20 to-emerald-500/20 opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight text-zinc-900">Welcome Back</h1>
                    <p className="text-zinc-500 mt-2 text-sm">Sign in to your dashboard.</p>
                </div>

                <div className="bg-white/90 backdrop-blur-2xl p-8 rounded-3xl shadow-[0_20px_50px_rgba(0,0,0,0.1)] border border-white ring-1 ring-zinc-100">
                    {passwordLoginDisabled && !forcePasswordForm && !mfaSetupToken ? (
                        <div className="space-y-4">
                            <p className="text-sm text-zinc-500 text-center">
                                This instance uses single sign-on.
                            </p>
                            {ssoProviders.map((p) => (
                                <a
                                    key={p.type}
                                    href={`${API}${p.login_path}`}
                                    className="w-full py-2.5 px-4 bg-zinc-900 hover:bg-zinc-800 text-white text-sm font-medium rounded-lg transition-all flex items-center justify-center gap-2 shadow-lg shadow-zinc-900/10"
                                >
                                    <Shield className="w-4 h-4" /> {p.label}
                                </a>
                            ))}
                            <a href="?password=1"
                               className="block text-center text-[11px] text-zinc-400 hover:text-zinc-600 transition-colors">
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
                    <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
                        <AnimatePresence>
                            {error && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0, overflow: 'hidden' }}
                                    animate={{ opacity: 1, height: "auto" }}
                                    exit={{ opacity: 0, height: 0 }}
                                    className="p-3 text-xs font-medium text-red-600 bg-red-50 border border-red-100 rounded-lg flex items-center gap-2"
                                >
                                    <AlertTriangle className="w-3 h-3" />
                                    {error}
                                </motion.div>
                            )}
                        </AnimatePresence>

                        {!mfaToken && (
                            <>
                                {ldapEnabled && (
                                    <div className="grid grid-cols-2 gap-1 p-1 bg-zinc-100 rounded-lg">
                                        {(['password', 'ldap'] as const).map((mode) => (
                                            <button
                                                key={mode} type="button"
                                                onClick={() => setLoginMode(mode)}
                                                className={`py-1.5 text-[11px] font-semibold rounded-md transition-colors ${loginMode === mode ? 'bg-white text-zinc-900 shadow-sm' : 'text-zinc-500 hover:text-zinc-700'}`}
                                            >
                                                {mode === 'password' ? 'Email' : 'Corporate (LDAP)'}
                                            </button>
                                        ))}
                                    </div>
                                )}
                                <div className="space-y-2">
                                    <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">
                                        {loginMode === 'ldap' ? 'Corporate username' : 'Work Email'}
                                    </label>
                                    <input
                                        {...register("email", { required: "Required" })}
                                        type={loginMode === 'ldap' ? 'text' : 'email'}
                                        autoFocus
                                        className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                        placeholder={loginMode === 'ldap' ? 'jsmith' : 'name@company.com'}
                                    />
                                    {errors.email && <span className="text-xs text-red-500">{errors.email.message as string}</span>}
                                </div>

                                <div className="space-y-2">
                                    <div className="flex justify-between items-center">
                                        <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Password</label>
                                        {loginMode !== 'ldap' && (
                                        <Link to="/forgot-password" className="text-[10px] font-medium text-zinc-400 hover:text-zinc-900 transition-colors">Forgot?</Link>
                                        )}
                                    </div>
                                    <input
                                        {...register("password", { required: "Required" })}
                                        type="password"
                                        className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                    />
                                    {errors.password && <span className="text-xs text-red-500">{errors.password.message as string}</span>}
                                </div>
                            </>
                        )}

                        {mfaToken && (
                            <div className="space-y-2">
                                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Authentication code</label>
                                <input
                                    value={mfaCode}
                                    onChange={(e) => setMfaCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                                    inputMode="numeric"
                                    autoFocus
                                    placeholder="123456"
                                    className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm tracking-[0.3em] text-center focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                />
                                <p className="text-[10px] text-zinc-400">Enter the 6-digit code from your authenticator app.</p>
                            </div>
                        )}

                        <button
                            type="submit"
                            disabled={isLoading}
                            className="w-full py-2.5 px-4 bg-zinc-900 hover:bg-zinc-800 text-white text-sm font-medium rounded-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center group shadow-lg shadow-zinc-900/10"
                        >
                            {isLoading ? <span className="flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Verifying…</span> : (
                                <span className="flex items-center">
                                    {mfaToken ? 'Verify' : 'Sign In'} <ArrowRight className="w-4 h-4 ml-2 opacity-50 group-hover:translate-x-1 group-hover:opacity-100 transition-all" />
                                </span>
                            )}
                        </button>

                        {ssoEnabled && !mfaToken && ssoProviders.map((p) => (
                            <a
                                key={p.type}
                                href={`${API}${p.login_path}`}
                                className="w-full py-2.5 px-4 border border-zinc-200 hover:bg-zinc-50 text-zinc-700 text-sm font-medium rounded-lg transition-all flex items-center justify-center gap-2"
                            >
                                <Shield className="w-4 h-4" /> {p.label}
                            </a>
                        ))}
                    </form>
                    )}
                </div>

                <div className="mt-8 text-center flex items-center justify-center gap-2">
                    <span className="text-sm text-zinc-500">New here?</span>
                    <Link to="/signup" className="text-sm font-medium text-zinc-900 hover:text-indigo-600 transition-colors">
                        Sign UP
                    </Link>
                </div>
            </motion.div>
        </SweepShell>
    );
}
