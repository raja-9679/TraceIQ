import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, ArrowRight, Code2, Globe, Database, Check, Workflow, GitBranch, Users, ShieldCheck } from "lucide-react";
import MfaEnrollment from "@/components/MfaEnrollment";
import SweepShell from "@/components/auth-sweep/SweepShell";

export default function Signup() {
    const { login } = useAuth();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const inviteToken = searchParams.get("token");
    const inviteEmail = searchParams.get("email");

    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    // Post-registration 2FA step. Optional by default ("secure your account");
    // mandatory (no skip) when the instance enforces MFA_REQUIRED, in which
    // case mfaStep.token is the enrollment challenge and verify returns the
    // session tokens.
    const [mfaStep, setMfaStep] = useState<{
        token: string; mandatory: boolean; user: any;
    } | null>(null);

    const { register, handleSubmit, watch, formState: { errors } } = useForm({
        defaultValues: {
            email: inviteEmail || "",
            fullName: "",
            password: "",
            confirmPassword: "",
            orgName: "",
            projectName: ""
        }
    });
    const password = watch("password");

    const onSubmit = async (data: any) => {
        setIsLoading(true);
        setError("");
        try {
            // Register
            const payload: any = {
                email: data.email,
                password: data.password,
                full_name: data.fullName,
                organization_name: data.orgName,
                project_name: data.projectName
            };

            if (inviteToken) {
                payload.invite_token = inviteToken;
            }

            const registerResponse = await axios.post(`${API_BASE_URL}/auth/register`, payload);

            // Login automatically after registration
            const formData = new FormData();
            formData.append('username', data.email);
            formData.append('password', data.password);

            const loginResponse = await axios.post(`${API_BASE_URL}/auth/login`, formData);

            if (loginResponse.data.mfa_setup_required) {
                // Instance enforces MFA: no session yet, enrollment is mandatory.
                setMfaStep({
                    token: loginResponse.data.mfa_token,
                    mandatory: true,
                    user: registerResponse.data,
                });
                return;
            }

            const { access_token, refresh_token } = loginResponse.data;
            login(access_token, registerResponse.data, refresh_token);
            // Offer (optional) 2FA enrollment before entering the app.
            setMfaStep({ token: access_token, mandatory: false, user: registerResponse.data });
        } catch (err: any) {
            console.error("Registration failed", err);
            setError(err.response?.data?.detail || "Registration failed. Please try again.");
        } finally {
            setIsLoading(false);
        }
    };

    // ... existing benefits array ...

    // ... inside render ...

    <div className="grid grid-cols-2 gap-3">
        {!inviteToken && (
            <>
                <div className="space-y-2">
                    <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Org Name (Optional)</label>
                    <input
                        {...register("orgName")}
                        className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                        placeholder="My Company"
                    />
                </div>
                <div className="space-y-2">
                    <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Project Name (Optional)</label>
                    <input
                        {...register("projectName")}
                        className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                        placeholder="Alpha"
                    />
                </div>
            </>
        )}
        {inviteToken && (
            <div className="col-span-2 p-3 bg-blue-50 text-blue-700 text-sm rounded-lg border border-blue-100 flex items-center justify-center">
                <Check className="w-4 h-4 mr-2" />
                Joining via Invitation
            </div>
        )}
    </div>
    return (
        <SweepShell
            badge="Quality intelligence platform"
            title={<>Start verifying every release in minutes.</>}
            subtitle="Record a journey once. TraceIQ re-runs it across browsers, APIs and devices on every change, and heals what drifts."
            points={[
                { icon: Workflow, title: "Record once, run everywhere", desc: "Capture a journey and replay it across browsers, devices and environments." },
                { icon: GitBranch, title: "Gate every pull request", desc: "CI, GitHub Action and agent APIs block regressions before they ship." },
                { icon: Users, title: "Built for teams", desc: "Workspaces, teams and project-level roles from day one." },
                { icon: ShieldCheck, title: "Secure by design", desc: "Encrypted secrets, data redaction and a tamper-evident audit trail." },
            ]}
            trust={["Self-hosted or cloud", "SSO-ready", "Role-based access"]}
        >
            <motion.div
                initial={{ opacity: 0, scale: 0.95 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.4 }}
                className="w-full max-w-[400px]"
            >
                <div className="flex flex-col items-center mb-8">
                    <div className="flex items-center gap-3 mb-6">
                        <div className="p-2 bg-white rounded-lg shadow-sm border border-zinc-100">
                            <Code2 className="w-5 h-5 text-indigo-500" />
                        </div>
                        <ArrowRight className="w-4 h-4 text-zinc-300" />
                        <div className="p-2 bg-white rounded-lg shadow-sm border border-zinc-100">
                            <Database className="w-5 h-5 text-emerald-500" />
                        </div>
                        <ArrowRight className="w-4 h-4 text-zinc-300" />
                        <div className="p-2 bg-white rounded-lg shadow-sm border border-zinc-100">
                            <Globe className="w-5 h-5 text-blue-500" />
                        </div>
                    </div>
                    <h1 className="text-2xl font-bold tracking-tight text-zinc-900">Create Account</h1>
                    <p className="text-zinc-500 mt-2 text-sm">Join thousands of developers.</p>
                </div>

                <div className="bg-white/90 backdrop-blur-2xl p-8 rounded-3xl shadow-[0_20px_50px_rgba(0,0,0,0.1)] border border-white ring-1 ring-zinc-100">
                    {mfaStep ? (
                        <MfaEnrollment
                            token={mfaStep.token}
                            onSkip={mfaStep.mandatory ? undefined : () => navigate("/")}
                            onCancel={mfaStep.mandatory ? () => {
                                setMfaStep(null);
                                navigate("/login");
                            } : undefined}
                            onExpired={() => {
                                setMfaStep(null);
                                navigate("/login", {
                                    state: { message: "Your enrollment session expired — please sign in again." },
                                });
                            }}
                            onComplete={(verify) => {
                                if (mfaStep.mandatory) {
                                    if (verify.access_token) {
                                        // Enrollment unblocked the session — store it now.
                                        login(verify.access_token, mfaStep.user, verify.refresh_token);
                                        navigate("/");
                                    } else {
                                        // No session issued — can't enter the app; send to login.
                                        navigate("/login", {
                                            state: { message: "Two-factor authentication is enabled. Please sign in." },
                                        });
                                    }
                                } else {
                                    navigate("/");
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
                                    className="p-3 text-xs font-medium text-red-600 bg-red-50 border border-red-100 rounded-lg"
                                >
                                    {error}
                                </motion.div>
                            )}
                        </AnimatePresence>

                        <div className="space-y-2">
                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Full Name</label>
                            <input
                                {...register("fullName", { required: "Required" })}
                                className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                placeholder="Name"
                            />
                            {errors.fullName && <span className="text-xs text-red-500">{errors.fullName.message as string}</span>}
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-2">
                                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Org Name (Optional)</label>
                                <input
                                    {...register("orgName")}
                                    className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                    placeholder="My Company"
                                />
                            </div>
                            <div className="space-y-2">
                                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Project Name (Optional)</label>
                                <input
                                    {...register("projectName")}
                                    className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                    placeholder="Alpha"
                                />
                            </div>
                        </div>

                        <div className="space-y-2">
                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Work Email</label>
                            <input
                                {...register("email", {
                                    required: "Required",
                                    pattern: { value: /^\S+@\S+$/i, message: "Invalid email" }
                                })}
                                type="email"
                                className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                placeholder="name@company.com"
                            />
                            {errors.email && <span className="text-xs text-red-500">{errors.email.message as string}</span>}
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-2">
                                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Password</label>
                                <input
                                    {...register("password", {
                                        required: "Required",
                                        minLength: { value: 6, message: "Min 6 chars" }
                                    })}
                                    type="password"
                                    className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                />
                                {errors.password && <span className="text-xs text-red-500">{errors.password.message as string}</span>}
                            </div>
                            <div className="space-y-2">
                                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Confirm</label>
                                <input
                                    {...register("confirmPassword", {
                                        required: "Required",
                                        validate: (val: string) => val === password || "Mismatch"
                                    })}
                                    type="password"
                                    className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                />
                                {errors.confirmPassword && <span className="text-xs text-red-500">{errors.confirmPassword.message as string}</span>}
                            </div>
                        </div>

                        <button
                            type="submit"
                            disabled={isLoading}
                            className="w-full py-2.5 px-4 bg-zinc-900 hover:bg-zinc-800 text-white text-sm font-medium rounded-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center group shadow-lg shadow-zinc-900/10"
                        >
                            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : (
                                <span className="flex items-center">
                                    Create Account <ArrowRight className="w-4 h-4 ml-2 opacity-50 group-hover:translate-x-1 group-hover:opacity-100 transition-all" />
                                </span>
                            )}
                        </button>
                    </form>
                    )}
                </div>

                <div className="mt-8 text-center">
                    <p className="text-sm text-zinc-500">
                        Already have an account?{" "}
                        <Link to="/login" className="text-zinc-900 font-medium hover:underline underline-offset-4 decoration-zinc-300">
                            Sign in
                        </Link>
                    </p>
                </div>
            </motion.div>
        </SweepShell>
    );
}
