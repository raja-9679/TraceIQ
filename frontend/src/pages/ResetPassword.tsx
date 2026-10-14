import { useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";

export default function ResetPassword() {
    const [params] = useSearchParams();
    const navigate = useNavigate();
    const token = params.get("token") || "";
    const [password, setPassword] = useState("");
    const [confirm, setConfirm] = useState("");
    const [submitting, setSubmitting] = useState(false);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (password.length < 8) {
            toast.error("Password must be at least 8 characters");
            return;
        }
        if (password !== confirm) {
            toast.error("Passwords do not match");
            return;
        }
        setSubmitting(true);
        try {
            await api.post("/auth/reset-password", { token, new_password: password });
            toast.success("Password updated. Please sign in.");
            navigate("/login");
        } catch (err) {
            const detail =
                (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
            toast.error(detail || "Invalid or expired reset link");
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-zinc-50 px-4">
            <div className="w-full max-w-sm bg-white border border-zinc-200 rounded-xl shadow-sm p-8">
                <h1 className="text-lg font-semibold text-zinc-900">Choose a new password</h1>
                {!token ? (
                    <p className="mt-4 text-sm text-red-500">Missing reset token. Use the link from your email.</p>
                ) : (
                    <form onSubmit={submit} className="mt-6 space-y-4">
                        <div className="space-y-2">
                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">New Password</label>
                            <input
                                type="password"
                                required
                                autoFocus
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                            />
                        </div>
                        <div className="space-y-2">
                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Confirm Password</label>
                            <input
                                type="password"
                                required
                                value={confirm}
                                onChange={(e) => setConfirm(e.target.value)}
                                className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={submitting}
                            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-800 transition-colors disabled:opacity-60"
                        >
                            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                            Update password
                        </button>
                    </form>
                )}
                <div className="mt-6 text-center">
                    <Link to="/login" className="text-xs font-medium text-zinc-400 hover:text-zinc-900 transition-colors">Back to sign in</Link>
                </div>
            </div>
        </div>
    );
}
