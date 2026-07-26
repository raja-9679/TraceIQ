import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { Loader2, CheckCircle2, XCircle } from "lucide-react";

export default function VerifyEmail() {
    const [params] = useSearchParams();
    const token = params.get("token") || "";
    const [state, setState] = useState<"loading" | "ok" | "error">(token ? "loading" : "error");

    useEffect(() => {
        if (!token) return;
        let cancelled = false;
        api.post("/auth/verify-email", { token })
            .then(() => { if (!cancelled) setState("ok"); })
            .catch(() => { if (!cancelled) setState("error"); });
        return () => { cancelled = true; };
    }, [token]);

    return (
        <div className="min-h-screen flex items-center justify-center bg-zinc-50 px-4">
            <div className="w-full max-w-sm bg-white border border-zinc-200 rounded-xl shadow-sm p-8 text-center">
                {state === "loading" && (
                    <div className="flex flex-col items-center gap-3 text-zinc-600">
                        <Loader2 className="w-6 h-6 animate-spin" />
                        <p className="text-sm">Verifying your email…</p>
                    </div>
                )}
                {state === "ok" && (
                    <div className="flex flex-col items-center gap-3">
                        <CheckCircle2 className="w-8 h-8 text-emerald-500" />
                        <h1 className="text-lg font-semibold text-zinc-900">Email verified</h1>
                        <p className="text-sm text-zinc-600">Your email address has been confirmed.</p>
                    </div>
                )}
                {state === "error" && (
                    <div className="flex flex-col items-center gap-3">
                        <XCircle className="w-8 h-8 text-red-500" />
                        <h1 className="text-lg font-semibold text-zinc-900">Verification failed</h1>
                        <p className="text-sm text-zinc-600">This link is invalid or has expired.</p>
                    </div>
                )}
                <div className="mt-6">
                    <Link to="/" className="text-xs font-medium text-zinc-400 hover:text-zinc-900 transition-colors">Go to dashboard</Link>
                </div>
            </div>
        </div>
    );
}
