import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, ArrowRight, Code2, Globe, Database, Rocket, Users, Lock, Check } from "lucide-react";

export default function Signup() {
    const { login } = useAuth();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const inviteToken = searchParams.get("token");
    const inviteEmail = searchParams.get("email");

    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");

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

            const registerResponse = await axios.post(`${import.meta.env.VITE_API_BASE_URL}/auth/register`, payload);

            // Login automatically after registration
            const formData = new FormData();
            formData.append('username', data.email);
            formData.append('password', data.password);

            const loginResponse = await axios.post(`${import.meta.env.VITE_API_BASE_URL}/auth/login`, formData);
            const { access_token } = loginResponse.data;

            login(access_token, registerResponse.data);
            navigate("/");
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
    const benefits = [
        {
            icon: Rocket,
            title: "Deploy 10x Faster",
            desc: "Automate your entire testing pipeline and release with confidence."
        },
        {
            icon: Users,
            title: "Built for Teams",
            desc: "Collaborative dashboards, shared workspaces, and granular permissions."
        },
        {
            icon: Lock,
            title: "Secure by Design",
            desc: "Single Sign-On (SSO), Audit Logs, and Private Cloud options available."
        }
    ];

    // CI/CD Pipeline Simulation
    const nodes = [
        // Source
        { id: 1, x: 10, y: 50, label: "git push", status: "success" },

        // Build Stage
        { id: 2, x: 25, y: 30, label: "Build (Go)", status: "success" },
        { id: 3, x: 25, y: 50, label: "Build (Node)", status: "success" },
        { id: 4, x: 25, y: 70, label: "Linting", status: "running" },

        // Test Matrix
        { id: 5, x: 45, y: 20, label: "Unit Tests", status: "success" },
        { id: 6, x: 45, y: 40, label: "Integration", status: "running" },
        { id: 7, x: 45, y: 60, label: "Sec-Scan", status: "pending" },
        { id: 8, x: 45, y: 80, label: "E2E-Cypress", status: "pending" },

        // Browser Grid
        { id: 9, x: 65, y: 30, label: "Chrome 120", status: "pending" },
        { id: 10, x: 65, y: 50, label: "Firefox 118", status: "pending" },
        { id: 11, x: 65, y: 70, label: "Safari 17", status: "pending" },

        // Deploy
        { id: 12, x: 85, y: 50, label: "Deploy: Staging", status: "pending" },
        { id: 13, x: 85, y: 80, label: "Notify: Slack", status: "pending" },
    ];

    const edges = [
        // Source -> Build
        { from: 1, to: 2 }, { from: 1, to: 3 }, { from: 1, to: 4 },

        // Build -> Test Matrix
        { from: 2, to: 5 }, { from: 2, to: 6 },
        { from: 3, to: 5 }, { from: 3, to: 6 },
        { from: 4, to: 7 },

        // Test -> Browser Grid
        { from: 6, to: 8 },
        { from: 8, to: 9 }, { from: 8, to: 10 }, { from: 8, to: 11 },

        // Grid -> Deploy
        { from: 9, to: 12 }, { from: 10, to: 12 }, { from: 11, to: 12 },

        // Side effects
        { from: 12, to: 13 }
    ];

    return (
        <div className="min-h-screen w-full flex bg-[#FAFAFA] text-zinc-900 font-inter relative overflow-hidden">
            {/* Background Flow Diagram */}
            <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
                <svg className="w-full h-full opacity-[0.05]">
                    <defs>
                        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#e4e4e7" strokeWidth="0.5" />
                        </pattern>
                    </defs>
                    <rect width="100%" height="100%" fill="url(#grid)" />

                    {edges.map((edge, i) => {
                        const start = nodes.find(n => n.id === edge.from);
                        const end = nodes.find(n => n.id === edge.to);
                        if (!start || !end) return null;

                        return (
                            <g key={i}>
                                <motion.line
                                    x1={`${start.x}%`} y1={`${start.y}%`}
                                    x2={`${end.x}%`} y2={`${end.y}%`}
                                    stroke="#64748B"
                                    strokeWidth="2"
                                    strokeOpacity="0.8"
                                />
                                <motion.circle
                                    r="3"
                                    fill="#3B82F6"
                                    initial={{ cx: `${start.x}%`, cy: `${start.y}%` }}
                                    animate={{ cx: `${end.x}%`, cy: `${end.y}%` }}
                                    transition={{
                                        duration: 1.5 + Math.random(),
                                        repeat: Infinity,
                                        ease: "linear",
                                        repeatDelay: Math.random() * 0.5
                                    }}
                                />
                            </g>
                        );
                    })}
                </svg>

                {nodes.map((node) => (
                    <motion.div
                        key={node.id}
                        className={`absolute flex items-center justify-center px-3 py-1.5 rounded-full border text-[10px] font-mono shadow-sm transition-colors duration-500 opacity-30
                            ${node.status === 'success' ? 'bg-blue-50 border-blue-200 text-blue-700' :
                                node.status === 'running' ? 'bg-indigo-50 border-indigo-200 text-indigo-700' :
                                    'bg-zinc-50 border-zinc-200 text-zinc-500'}
                        `}
                        style={{ left: `calc(${node.x}% - 50px)`, top: `calc(${node.y}% - 15px)` }}
                    >
                        <div className="flex items-center gap-1.5">
                            {node.status === 'success' && <Check className="w-3 h-3" />}
                            {node.status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
                            {node.status === 'pending' && <div className="w-2 h-2 rounded-full bg-zinc-300" />}
                            {node.label}
                        </div>
                    </motion.div>
                ))}
            </div>

            <div className="w-full max-w-7xl mx-auto flex items-center justify-center relative z-10 px-6 lg:px-12 h-screen">
                <div className="grid lg:grid-cols-2 gap-12 w-full items-center">

                    {/* Left Side: Explanatory Content */}
                    <div className="hidden lg:block space-y-8 p-8 rounded-3xl bg-white/40 backdrop-blur-md border border-white/60 shadow-[0_8px_32px_rgba(0,0,0,0.05)] order-2 lg:order-1 relative overflow-hidden">

                        {/* Subtle decorative gradient inside card */}
                        <div className="absolute top-0 left-0 w-64 h-64 bg-blue-500/5 rounded-full blur-3xl -z-10" />

                        <div className="space-y-4">
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="inline-block"
                            >
                                <span className="px-3 py-1 bg-blue-600 text-white rounded-full text-xs font-bold uppercase tracking-wide shadow-lg shadow-blue-500/20">Early Access</span>
                            </motion.div>
                            <motion.h1
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.1 }}
                                className="text-4xl xl:text-5xl font-bold tracking-tight text-zinc-900 drop-shadow-sm"
                            >
                                Start testing in <br /> <span className="text-indigo-600">seconds, not days.</span>
                            </motion.h1>
                            <motion.p
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.2 }}
                                className="text-lg text-zinc-600 max-w-md leading-relaxed font-medium"
                            >
                                Create an account to access the most powerful automated quality intelligence platform on the market.
                            </motion.p>
                        </div>

                        <div className="space-y-4">
                            {benefits.map((item, i) => (
                                <motion.div
                                    key={i}
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: 0.3 + (i * 0.1) }}
                                    className="flex items-start gap-4 p-4 rounded-xl bg-white/60 border border-white/80 shadow-sm hover:shadow-md transition-all duration-300"
                                >
                                    <div className="p-2.5 bg-white rounded-lg shadow-sm border border-zinc-100/50">
                                        <item.icon className="w-5 h-5 text-indigo-600" />
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-zinc-900">{item.title}</h3>
                                        <p className="text-sm text-zinc-500 font-medium">{item.desc}</p>
                                    </div>
                                </motion.div>
                            ))}
                        </div>
                    </div>


                    {/* Right Side: Signup Form */}
                    <div className="flex justify-center lg:justify-end order-1 lg:order-2">
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
                    </div>
                </div>
            </div>
        </div>
    );
}
