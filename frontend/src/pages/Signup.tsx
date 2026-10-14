import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, Check, Workflow, GitBranch, Users, ShieldCheck } from "lucide-react";
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
                <div className="space-y-1.5">
                    <label className="text-sm font-medium text-zinc-700">Organization <span className="font-normal text-zinc-400">(optional)</span></label>
                    <input
                        {...register("orgName")}
                        className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                        placeholder="My Company"
                    />
                </div>
                <div className="space-y-1.5">
                    <label className="text-sm font-medium text-zinc-700">First project <span className="font-normal text-zinc-400">(optional)</span></label>
                    <input
                        {...register("projectName")}
                        className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                        placeholder="Alpha"
                    />
                </div>
            </>
        )}
        {inviteToken && (
            <div className="col-span-2 p-3 bg-sky-50 text-sky-800 text-sm rounded-md border border-sky-200 flex items-center justify-center">
                <Check className="w-4 h-4 mr-2" />
                Joining via Invitation
            </div>
        )}
    </div>
    return (
        <SweepShell
            title={<>Start verifying every release in minutes.</>}
            subtitle="Record a journey once. TraceIQ re-runs it across browsers, APIs and devices on every change, and heals what drifts."
            points={[
                { icon: Workflow, title: "Record once, run everywhere", desc: "Capture a journey and replay it across browsers, devices and environments." },
                { icon: GitBranch, title: "Gate every pull request", desc: "CI, GitHub Action and agent APIs block regressions before they ship." },
                { icon: Users, title: "Built for teams", desc: "Workspaces, teams and project-level roles from day one." },
                { icon: ShieldCheck, title: "Secure by design", desc: "Encrypted secrets, data redaction and a tamper-evident audit trail." },
            ]}
            trust={["Self-hosted or cloud", "SAML & OIDC single sign-on", "Role-based access"]}
            topLink={<>Already have an account? <Link to="/login" className="font-medium text-sky-700 hover:text-sky-900">Sign in</Link></>}
        >
            <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
                className="w-full"
            >
                <div className="mb-8">
                    <h1 className="text-2xl font-semibold tracking-tight text-zinc-900">Create your account</h1>
                    <p className="mt-1.5 text-sm text-zinc-500">Set up a workspace for your team.</p>
                </div>

                <div>
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
                    <form onSubmit={handleSubmit(onSubmit)} className="space-y-5">
                        <AnimatePresence>
                            {error && (
                                <motion.div
                                    initial={{ opacity: 0, height: 0, overflow: 'hidden' }}
                                    animate={{ opacity: 1, height: "auto" }}
                                    exit={{ opacity: 0, height: 0 }}
                                    className="p-3 text-sm text-red-700 bg-red-50 border border-red-200 rounded-md"
                                >
                                    {error}
                                </motion.div>
                            )}
                        </AnimatePresence>

                        <div className="space-y-1.5">
                            <label className="text-sm font-medium text-zinc-700">Full name</label>
                            <input
                                {...register("fullName", { required: "Required" })}
                                className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                placeholder="Name"
                            />
                            {errors.fullName && <span className="text-xs text-red-500">{errors.fullName.message as string}</span>}
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-zinc-700">Organization <span className="font-normal text-zinc-400">(optional)</span></label>
                                <input
                                    {...register("orgName")}
                                    className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                    placeholder="My Company"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-zinc-700">First project <span className="font-normal text-zinc-400">(optional)</span></label>
                                <input
                                    {...register("projectName")}
                                    className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                    placeholder="Alpha"
                                />
                            </div>
                        </div>

                        <div className="space-y-1.5">
                            <label className="text-sm font-medium text-zinc-700">Work email</label>
                            <input
                                {...register("email", {
                                    required: "Required",
                                    pattern: { value: /^\S+@\S+$/i, message: "Invalid email" }
                                })}
                                type="email"
                                className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                placeholder="name@company.com"
                            />
                            {errors.email && <span className="text-xs text-red-500">{errors.email.message as string}</span>}
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-zinc-700">Password</label>
                                <input
                                    {...register("password", {
                                        required: "Required",
                                        minLength: { value: 6, message: "Min 6 chars" }
                                    })}
                                    type="password"
                                    className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                />
                                {errors.password && <span className="text-xs text-red-500">{errors.password.message as string}</span>}
                            </div>
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium text-zinc-700">Confirm password</label>
                                <input
                                    {...register("confirmPassword", {
                                        required: "Required",
                                        validate: (val: string) => val === password || "Mismatch"
                                    })}
                                    type="password"
                                    className="w-full h-10 px-3 rounded-md bg-white border border-zinc-300 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-sky-500/20 focus:border-sky-600 transition-colors"
                                />
                                {errors.confirmPassword && <span className="text-xs text-red-500">{errors.confirmPassword.message as string}</span>}
                            </div>
                        </div>

                        <button
                            type="submit"
                            disabled={isLoading}
                            className="w-full h-10 px-4 bg-[#0B1220] hover:bg-[#16213A] text-white text-sm font-medium rounded-md transition-colors disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center"
                        >
                            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : (
                                <span>Create account</span>
                            )}
                        </button>
                    </form>
                    )}
                </div>
            </motion.div>
        </SweepShell>
    );
}
