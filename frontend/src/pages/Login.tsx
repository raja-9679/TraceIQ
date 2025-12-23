import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, ArrowRight, LayoutGrid, Check, X, AlertTriangle, Terminal, Zap, Shield, Workflow } from "lucide-react";

export default function Login() {
    const { login } = useAuth();
    const navigate = useNavigate();
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");

    const { register, handleSubmit, formState: { errors } } = useForm();

    // Complex Microservices Architecture Simulation
    const initialNodes = [
        // Entry Points & LB
        { id: 1, x: 50, y: 8, label: "Global LB", status: "success" },
        { id: 2, x: 50, y: 18, label: "API Gateway", status: "success" },

        // Core Microservices Mesh
        { id: 3, x: 25, y: 30, label: "Auth Service", status: "success" },
        { id: 4, x: 75, y: 30, label: "Payment Core", status: "success" },
        { id: 5, x: 40, y: 40, label: "Order Service", status: "running" },
        { id: 6, x: 60, y: 40, label: "Inventory", status: "success" },
        { id: 7, x: 50, y: 55, label: "Notification", status: "pending" },

        // Data & Caching Layer
        { id: 8, x: 15, y: 45, label: "User DB (P)", status: "success" },
        { id: 9, x: 85, y: 45, label: "Ledger DB", status: "success" },
        { id: 10, x: 50, y: 30, label: "Redis Cluster", status: "running" },

        // Testing Infrastructure (The "Intelligence")
        { id: 11, x: 15, y: 70, label: "Test Runner 1", status: "running" },
        { id: 12, x: 35, y: 75, label: "Test Runner 2", status: "pending" },
        { id: 13, x: 65, y: 75, label: "Test Runner 3", status: "success" },
        { id: 14, x: 85, y: 70, label: "Test Runner 4", status: "error" },

        // Monitoring & External
        { id: 15, x: 90, y: 15, label: "Stripe API", status: "success" },
        { id: 16, x: 10, y: 15, label: "Auth0", status: "success" },
        { id: 17, x: 50, y: 85, label: "Observability", status: "running" }
    ];

    const [nodes, setNodes] = useState(initialNodes);
    const [logs, setLogs] = useState<string[]>([]);

    useEffect(() => {
        const interval = setInterval(() => {
            setNodes(prev => prev.map(node => {
                // Randomly flicker status for "live" feel
                if (Math.random() > 0.85) {
                    const statuses = ["success", "running", "running", "success", "pending"]; // bias towards active
                    const newStatus = statuses[Math.floor(Math.random() * statuses.length)];
                    return { ...node, status: newStatus };
                }
                return node;
            }));

            // Generate Log
            const actions = ["Compiling", "Verifying", "Connecting", "Refactoring", "Deploying", "Testing", "Tracing"];
            const targets = ["UserAPI", "PaymentGateway", "FrontendWrapper", "DBCluster", "CacheLayer", "WorkerNode"];
            const newLog = `> [${new Date().toLocaleTimeString()}] ${actions[Math.floor(Math.random() * actions.length)]} module: ${targets[Math.floor(Math.random() * targets.length)]}...`;
            setLogs(prev => [newLog, ...prev].slice(0, 8));

        }, 1500); // Faster updates
        return () => clearInterval(interval);
    }, []);


    const onSubmit = async (data: any) => {
        setIsLoading(true);
        setError("");
        try {
            const formData = new FormData();
            formData.append('username', data.email);
            formData.append('password', data.password);

            const response = await axios.post("http://localhost:8000/api/auth/login", formData);
            const { access_token } = response.data;

            const userResponse = await axios.get("http://localhost:8000/api/auth/me", {
                headers: { Authorization: `Bearer ${access_token}` }
            });

            login(access_token, userResponse.data);
            navigate("/");
        } catch (err: any) {
            console.error("Login failed", err);
            setError(err.response?.data?.detail || "Invalid credentials.");
        } finally {
            setIsLoading(false);
        }
    };

    const edges = [
        // Ingress Flow
        { from: 1, to: 2 }, // LB -> Gateway

        // Service Dependencies
        { from: 2, to: 3 }, // Gateway -> Auth
        { from: 2, to: 4 }, // Gateway -> Payment
        { from: 2, to: 5 }, // Gateway -> Order
        { from: 2, to: 6 }, // Gateway -> Inventory

        // Inner Mesh
        { from: 3, to: 10 }, // Auth -> Redis
        { from: 5, to: 6 },  // Order -> Inventory
        { from: 5, to: 4 },  // Order -> Payment
        { from: 5, to: 10 }, // Order -> Redis

        // Data Access
        { from: 3, to: 8 },  // Auth -> User DB
        { from: 4, to: 9 },  // Payment -> Ledger DB
        { from: 6, to: 9 },  // Inventory -> Ledger DB

        // External
        { from: 3, to: 16 }, // Auth -> Auth0
        { from: 4, to: 15 }, // Payment -> Stripe

        // Test Coverage (The "Complex Arch Testing" part)
        // Runners probing services
        { from: 11, to: 3 }, // Runner 1 -> Auth
        { from: 11, to: 8 }, // Runner 1 -> User DB

        { from: 12, to: 5 }, // Runner 2 -> Order
        { from: 12, to: 2 }, // Runner 2 -> Gateway

        { from: 13, to: 4 }, // Runner 3 -> Payment
        { from: 13, to: 9 }, // Runner 3 -> Ledger

        { from: 14, to: 6 }, // Runner 4 -> Inventory
        { from: 14, to: 7 }, // Runner 4 -> Notification

        // Monitoring Aggregation
        { from: 17, to: 1 }, // Observability -> LB
        { from: 17, to: 8 },
        { from: 17, to: 9 },
    ];

    const features = [
        {
            icon: Zap,
            title: "Lightning Fast Execution",
            desc: "Distributed test runner optimized for speed and parallel execution."
        },
        {
            icon: Shield,
            title: "Enterprise Grade Security",
            desc: "SOC2 compliant infrastructure with end-to-end encryption."
        },
        {
            icon: Workflow,
            title: "Visual Flow Builder",
            desc: "Design complex automation flows with our intuitive drag-and-drop interface."
        }
    ];

    return (
        <div className="min-h-screen w-full flex bg-[#FAFAFA] text-zinc-900 font-inter relative overflow-hidden">

            {/* Live Terminal Background - Fixed Position */}
            <motion.div
                className="absolute bottom-10 right-10 z-0 p-4 font-mono text-[10px] text-zinc-400 bg-white/50 backdrop-blur-sm rounded-lg border border-zinc-200 shadow-sm w-64 hidden xl:block"
                initial={{ opacity: 0, x: 50 }}
                animate={{ opacity: 1, x: 0 }}
            >
                <div className="flex items-center gap-2 mb-2 text-zinc-500 border-b border-zinc-100 pb-1">
                    <Terminal className="w-3 h-3" />
                    <span>System Activity</span>
                </div>
                <div className="space-y-1">
                    <AnimatePresence>
                        {logs.map((log, i) => (
                            <motion.div
                                key={i}
                                initial={{ opacity: 0, x: -10 }}
                                animate={{ opacity: 1 - (i * 0.15), x: 0 }}
                                exit={{ opacity: 0 }}
                                className="truncate"
                            >
                                {log}
                            </motion.div>
                        ))}
                    </AnimatePresence>
                </div>
            </motion.div>

            {/* Background Flow Diagram */}
            <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none">
                <svg className="w-full h-full opacity-[0.06]">
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
                                    strokeOpacity="0.6"
                                />
                                {/* Fast Moving Data Packets */}
                                <motion.circle
                                    r="3"
                                    fill={i % 2 === 0 ? "#10B981" : "#6366F1"}
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
                        layout
                        className={`absolute flex items-center justify-center px-3 py-1.5 rounded-full border text-[10px] font-mono shadow-sm transition-colors duration-500 opacity-30
                            ${node.status === 'success' ? 'bg-emerald-50 border-emerald-200 text-emerald-700' :
                                node.status === 'error' ? 'bg-red-50 border-red-200 text-red-700' :
                                    node.status === 'running' ? 'bg-indigo-50 border-indigo-200 text-indigo-700' :
                                        'bg-zinc-50 border-zinc-200 text-zinc-500'}
                        `}
                        style={{ left: `calc(${node.x}% - 40px)`, top: `calc(${node.y}% - 15px)` }}
                    >
                        <div className="flex items-center gap-1.5">
                            {node.status === 'success' && <Check className="w-3 h-3" />}
                            {node.status === 'error' && <X className="w-3 h-3" />}
                            {node.status === 'running' && <Loader2 className="w-3 h-3 animate-spin" />}
                            {node.status === 'pending' && <div className="w-2 h-2 rounded-full bg-zinc-300" />}
                            {node.status === 'warning' && <AlertTriangle className="w-3 h-3" />}
                            {node.label}
                        </div>
                    </motion.div>
                ))}
            </div>

            <div className="w-full max-w-7xl mx-auto flex items-center justify-center relative z-10 px-6 lg:px-12 h-screen">
                <div className="grid lg:grid-cols-2 gap-12 w-full items-center">

                    {/* Left Side: Text & Value Props */}
                    <div className="hidden lg:block space-y-8 p-8 rounded-3xl bg-white/40 backdrop-blur-md border border-white/60 shadow-[0_8px_32px_rgba(0,0,0,0.05)] relative overflow-hidden">

                        {/* Subtle decorative gradient inside card */}
                        <div className="absolute top-0 right-0 w-64 h-64 bg-indigo-500/5 rounded-full blur-3xl -z-10" />

                        <div className="space-y-4">
                            <motion.div
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                className="inline-block"
                            >
                                <span className="px-3 py-1 bg-zinc-900 text-white rounded-full text-xs font-medium shadow-lg shadow-zinc-900/20">TraceIQ v3.0</span>
                            </motion.div>
                            <motion.h1
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.1 }}
                                className="text-4xl xl:text-5xl font-bold tracking-tight text-zinc-900 drop-shadow-sm"
                            >
                                Quality intelligence associated with speed.
                            </motion.h1>
                            <motion.p
                                initial={{ opacity: 0, y: 20 }}
                                animate={{ opacity: 1, y: 0 }}
                                transition={{ delay: 0.2 }}
                                className="text-lg text-zinc-600 max-w-md leading-relaxed font-medium"
                            >
                                The complete platform for automated testing, visual regression, and performance monitoring.
                            </motion.p>
                        </div>

                        <div className="space-y-4">
                            {features.map((feature, i) => (
                                <motion.div
                                    key={i}
                                    initial={{ opacity: 0, x: -20 }}
                                    animate={{ opacity: 1, x: 0 }}
                                    transition={{ delay: 0.3 + (i * 0.1) }}
                                    className="flex items-start gap-4 p-4 rounded-xl bg-white/60 border border-white/80 shadow-sm hover:shadow-md transition-all duration-300"
                                >
                                    <div className="p-2.5 bg-white rounded-lg shadow-sm border border-zinc-100/50">
                                        <feature.icon className="w-5 h-5 text-indigo-600" />
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-zinc-900">{feature.title}</h3>
                                        <p className="text-sm text-zinc-500 font-medium">{feature.desc}</p>
                                    </div>
                                </motion.div>
                            ))}
                        </div>
                    </div>

                    {/* Right Side: Login Form */}
                    <div className="flex justify-center lg:justify-end">
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

                                    <div className="space-y-2">
                                        <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Work Email</label>
                                        <input
                                            {...register("email", { required: "Required" })}
                                            type="email"
                                            autoFocus
                                            className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                            placeholder="name@company.com"
                                        />
                                        {errors.email && <span className="text-xs text-red-500">{errors.email.message as string}</span>}
                                    </div>

                                    <div className="space-y-2">
                                        <div className="flex justify-between items-center">
                                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Password</label>
                                            <a href="#" className="text-[10px] font-medium text-zinc-400 hover:text-zinc-900 transition-colors">Forgot?</a>
                                        </div>
                                        <input
                                            {...register("password", { required: "Required" })}
                                            type="password"
                                            className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                        />
                                        {errors.password && <span className="text-xs text-red-500">{errors.password.message as string}</span>}
                                    </div>

                                    <button
                                        type="submit"
                                        disabled={isLoading}
                                        className="w-full py-2.5 px-4 bg-zinc-900 hover:bg-zinc-800 text-white text-sm font-medium rounded-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center group shadow-lg shadow-zinc-900/10"
                                    >
                                        {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : (
                                            <span className="flex items-center">
                                                Sign In <ArrowRight className="w-4 h-4 ml-2 opacity-50 group-hover:translate-x-1 group-hover:opacity-100 transition-all" />
                                            </span>
                                        )}
                                    </button>
                                </form>
                            </div>

                            <div className="mt-8 text-center flex items-center justify-center gap-2">
                                <span className="text-sm text-zinc-500">New here?</span>
                                <Link to="/signup" className="text-sm font-medium text-zinc-900 hover:text-indigo-600 transition-colors">
                                    Start 14-day free trial
                                </Link>
                            </div>
                        </motion.div>
                    </div>
                </div>
            </div>

            {/* Footer Items */}
            <div className="absolute bottom-6 left-6 hidden lg:flex gap-6 text-[10px] uppercase tracking-wider text-zinc-400 font-medium z-10">
                <span>© 2025 TraceIQ Inc.</span>
            </div>
        </div>
    );
}
