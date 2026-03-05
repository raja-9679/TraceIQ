import { useQuery } from '@tanstack/react-query';
import { getRuns } from '@/lib/api';
import { CheckCircle2, XCircle, Clock, TrendingUp, Activity, ChevronRight, PlayCircle, BarChart3, TestTube } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { motion, Variants } from 'framer-motion';
import { cn, formatDate } from '@/lib/utils';
import { useAuth } from '@/context/AuthContext';

export default function Dashboard() {
    const { user } = useAuth();
    const navigate = useNavigate();
    const { data: runsData, isLoading } = useQuery({
        queryKey: ['runs'],
        queryFn: () => getRuns(),
        refetchInterval: 2000,
    });

    const runs = runsData?.runs || [];
    const stats = {
        total: runsData?.total || 0,
        passed: runs.filter(r => r.status === 'passed').length,
        failed: runs.filter(r => r.status === 'failed').length,
        running: runs.filter(r => r.status === 'running').length,
    };

    const passRate = stats.total > 0 ? ((stats.passed / stats.total) * 100).toFixed(1) : '0.0';

    const containerVariants: Variants = {
        hidden: { opacity: 0 },
        visible: {
            opacity: 1,
            transition: {
                staggerChildren: 0.1
            }
        }
    };

    const itemVariants: Variants = {
        hidden: { y: 20, opacity: 0 },
        visible: {
            y: 0,
            opacity: 1,
            transition: {
                type: "spring",
                stiffness: 100,
                damping: 15
            }
        }
    };

    if (isLoading) {
        return (
            <div className="space-y-8 max-w-7xl mx-auto pb-12 animate-pulse">
                {/* Hero skeleton */}
                <div className="rounded-3xl bg-white border border-gray-200 shadow-sm p-8 md:p-10">
                    <div className="w-36 h-5 bg-gray-100 rounded-full mb-4" />
                    <div className="w-72 h-9 bg-gray-100 rounded-lg mb-3" />
                    <div className="w-96 h-5 bg-gray-100 rounded-lg" />
                </div>

                {/* Metric cards skeleton */}
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                    {[...Array(4)].map((_, i) => (
                        <div key={i} className="bg-white rounded-3xl p-6 border border-gray-200 shadow-sm">
                            <div className="w-12 h-12 bg-gray-100 rounded-2xl mb-4" />
                            <div className="w-20 h-4 bg-gray-100 rounded mb-2" />
                            <div className="w-14 h-10 bg-gray-100 rounded mb-2" />
                            <div className="w-28 h-4 bg-gray-100 rounded" />
                        </div>
                    ))}
                </div>

                {/* Recent runs skeleton */}
                <div className="bg-white rounded-3xl border border-gray-200 shadow-sm overflow-hidden">
                    <div className="px-6 py-5 border-b border-gray-100 bg-gray-50/50">
                        <div className="w-40 h-5 bg-gray-100 rounded-lg" />
                    </div>
                    <div className="divide-y divide-gray-100">
                        {[...Array(5)].map((_, i) => (
                            <div key={i} className="p-5 flex items-center justify-between">
                                <div className="flex items-center gap-4">
                                    <div className="w-10 h-10 rounded-full bg-gray-100 shrink-0" />
                                    <div>
                                        <div className="w-48 h-4 bg-gray-100 rounded mb-2" />
                                        <div className="w-32 h-3 bg-gray-100 rounded" />
                                    </div>
                                </div>
                                <div className="flex items-center gap-6">
                                    <div className="w-16 h-8 bg-gray-100 rounded-full" />
                                    <div className="w-5 h-5 bg-gray-100 rounded" />
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        );
    }

    return (
        <motion.div
            className="space-y-8 max-w-7xl mx-auto pb-12"
            variants={containerVariants}
            initial="hidden"
            animate="visible"
        >
            {/* Hero / Welcome UI */}
            <motion.div variants={itemVariants} className="relative overflow-hidden rounded-3xl bg-white border border-gray-200 shadow-sm">
                <div className="absolute top-0 right-0 -mt-20 -mr-20 w-80 h-80 bg-gradient-to-br from-primary/10 to-transparent rounded-full blur-3xl opacity-60"></div>
                <div className="absolute bottom-0 left-0 -mb-20 -ml-20 w-60 h-60 bg-gradient-to-tr from-blue-500/5 to-transparent rounded-full blur-3xl opacity-60"></div>

                <div className="relative p-8 md:p-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
                    <div>
                        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/5 text-primary text-xs font-semibold uppercase tracking-wider mb-4 border border-primary/10">
                            <Activity size={14} /> Workspace Overview
                        </div>
                        <h1 className="text-3xl md:text-4xl font-bold text-gray-900 tracking-tight">
                            Welcome back, <span className="text-primary">{user?.full_name?.split(' ')[0] || 'User'}</span>
                        </h1>
                        <p className="text-gray-500 mt-2 text-lg max-w-xl">
                            Here is a high-level summary of your test automation platform's health and recent execution activity.
                        </p>
                    </div>
                </div>
            </motion.div>

            {/* Premium Metric Cards */}
            <motion.div variants={itemVariants} className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
                <MetricCard
                    title="Total Runs"
                    value={stats.total.toString()}
                    subtitle="All test executions"
                    icon={<TestTube size={22} className="text-gray-700" />}
                    iconBg="bg-gray-100"
                />
                <MetricCard
                    title="Passed"
                    value={stats.passed.toString()}
                    subtitle={`${passRate}% pass rate`}
                    icon={<CheckCircle2 size={22} className="text-emerald-700" />}
                    iconBg="bg-emerald-100"
                    trend="up"
                />
                <MetricCard
                    title="Failed"
                    value={stats.failed.toString()}
                    subtitle="Needs attention"
                    icon={<XCircle size={22} className="text-rose-700" />}
                    iconBg="bg-rose-100"
                    trend="down"
                />
                <MetricCard
                    title="Running"
                    value={stats.running.toString()}
                    subtitle="In progress"
                    icon={<Clock size={22} className="text-blue-700" />}
                    iconBg="bg-blue-100"
                    isSpinning={stats.running > 0}
                />
            </motion.div>

            {/* Recent Runs Table / List */}
            <motion.div variants={itemVariants} className="bg-white rounded-3xl border border-gray-200 shadow-sm overflow-hidden">
                <div className="px-6 py-5 border-b border-gray-100 flex items-center justify-between bg-gray-50/50">
                    <h2 className="text-lg font-bold text-gray-900 flex items-center gap-2">
                        <PlayCircle size={20} className="text-primary" /> Recent Test Runs
                    </h2>
                </div>

                <div className="divide-y divide-gray-100">
                    {runs.slice(0, 10).map((run, i) => (
                        <motion.div
                            key={run.id}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: i * 0.05 }}
                            onClick={() => navigate(`/runs/${run.id}`)}
                            className="group flex flex-col sm:flex-row sm:items-center justify-between p-5 hover:bg-gray-50 cursor-pointer transition-all duration-200"
                        >
                            <div className="flex items-start sm:items-center gap-4">
                                <div className={cn(
                                    "w-10 h-10 rounded-full flex items-center justify-center shrink-0 border shadow-sm transition-transform group-hover:scale-110",
                                    run.status === 'passed' ? "bg-emerald-50 border-emerald-100 text-emerald-600" :
                                        run.status === 'failed' || run.status === 'error' ? "bg-rose-50 border-rose-100 text-rose-600" :
                                            run.status === 'running' ? "bg-blue-50 border-blue-100 text-blue-600" :
                                                "bg-gray-100 border-gray-200 text-gray-500"
                                )}>
                                    {run.status === 'passed' && <CheckCircle2 size={20} />}
                                    {(run.status === 'failed' || run.status === 'error') && <XCircle size={20} />}
                                    {run.status === 'running' && <Clock size={20} className="animate-spin" />}
                                    {(run.status === 'pending') && <Clock size={20} />}
                                </div>

                                <div>
                                    <h3 className="font-semibold text-gray-900 group-hover:text-primary transition-colors flex items-center gap-2">
                                        {run.suite_name || `Run #${run.id}`}
                                        {run.test_case_name && (
                                            <span className="text-sm font-normal text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full border border-gray-200">
                                                {run.test_case_name}
                                            </span>
                                        )}
                                    </h3>
                                    <p className="text-xs text-gray-500 mt-1 flex items-center gap-2">
                                        <span className="font-mono">{formatDate(run.created_at)}</span>
                                    </p>
                                </div>
                            </div>

                            <div className="flex items-center gap-6 mt-4 sm:mt-0 pl-14 sm:pl-0">
                                {run.duration_ms && (
                                    <div className="text-right">
                                        <p className="text-[10px] uppercase font-bold text-gray-400 tracking-wider mb-0.5">Duration</p>
                                        <p className="font-mono text-sm text-gray-700">{(run.duration_ms / 1000).toFixed(2)}s</p>
                                    </div>
                                )}
                                <div className="text-right">
                                    <p className="text-[10px] uppercase font-bold text-gray-400 tracking-wider mb-0.5">Status</p>
                                    <span className={cn(
                                        "text-xs font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border inline-block",
                                        run.status === 'passed' ? "bg-emerald-50 text-emerald-700 border-emerald-200" :
                                            run.status === 'failed' || run.status === 'error' ? "bg-rose-50 text-rose-700 border-rose-200" :
                                                run.status === 'running' ? "bg-blue-50 text-blue-700 border-blue-200" :
                                                    "bg-gray-50 text-gray-700 border-gray-200"
                                    )}>
                                        {run.status}
                                    </span>
                                </div>
                                <div className="hidden sm:flex text-gray-300 group-hover:text-primary group-hover:translate-x-1 transition-all">
                                    <ChevronRight size={20} />
                                </div>
                            </div>
                        </motion.div>
                    ))}

                    {(!runs || runs.length === 0) && (
                        <div className="p-12 text-center flex flex-col items-center justify-center">
                            <div className="w-16 h-16 bg-gray-50 rounded-full flex items-center justify-center mb-4">
                                <BarChart3 size={28} className="text-gray-300" />
                            </div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-1">No execution history</h3>
                            <p className="text-gray-500 text-sm max-w-sm">When you execute Playwright test suites, they will elegantly appear here.</p>
                        </div>
                    )}
                </div>
            </motion.div >
        </motion.div >
    );
}

// Helper component for metric cards
function MetricCard({ title, value, subtitle, icon, iconBg, trend, isSpinning }: {
    title: string, value: string, subtitle: string, icon: React.ReactNode, iconBg: string, trend?: 'up' | 'down', isSpinning?: boolean
}) {
    return (
        <div className="bg-white rounded-3xl p-6 border border-gray-200 shadow-sm hover:shadow-md hover:-translate-y-1 transition-all duration-300">
            <div className="flex items-start justify-between mb-4">
                <div className={cn("w-12 h-12 rounded-2xl flex items-center justify-center shadow-inner", iconBg, isSpinning && 'animate-pulse')}>
                    {icon}
                </div>
                {trend && (
                    <div className={cn("px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider flex items-center gap-1", trend === 'up' ? 'text-emerald-700 bg-emerald-50 border border-emerald-100' : 'text-rose-700 bg-rose-50 border border-rose-100')}>
                        <TrendingUp size={12} className={cn(trend === 'down' && "rotate-180")} />
                        {trend === 'up' ? 'Good' : 'Needs Fix'}
                    </div>
                )}
            </div>
            <div>
                <h3 className="text-sm font-bold text-gray-500 uppercase tracking-wider mb-1">{title}</h3>
                <div className="flex items-baseline gap-2">
                    <p className="text-4xl font-black text-gray-900 tracking-tight">{value}</p>
                </div>
                <p className="text-sm font-medium text-gray-500 mt-2">{subtitle}</p>
            </div>
        </div>
    );
}
