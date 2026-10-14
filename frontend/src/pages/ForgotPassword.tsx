import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";

export default function ForgotPassword() {
    const [email, setEmail] = useState("");
    const [submitting, setSubmitting] = useState(false);
    const [done, setDone] = useState(false);

    const submit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post("/auth/forgot-password", { email });
            setDone(true);
        } catch {
            // The endpoint always succeeds; show the same confirmation regardless.
            setDone(true);
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-zinc-50 px-4">
            <div className="w-full max-w-sm bg-white border border-zinc-200 rounded-xl shadow-sm p-8">
                <h1 className="text-lg font-semibold text-zinc-900">Reset your password</h1>
                {done ? (
                    <p className="mt-4 text-sm text-zinc-600">
                        If an account exists for that email, we've sent a reset link. Check your inbox.
                    </p>
                ) : (
                    <form onSubmit={submit} className="mt-6 space-y-4">
                        <div className="space-y-2">
                            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Work Email</label>
                            <input
                                type="email"
                                required
                                autoFocus
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                                placeholder="name@company.com"
                            />
                        </div>
                        <button
                            type="submit"
                            disabled={submitting}
                            className="w-full flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-zinc-900 text-white text-sm font-medium hover:bg-zinc-800 transition-colors disabled:opacity-60"
                        >
                            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
                            Send reset link
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
