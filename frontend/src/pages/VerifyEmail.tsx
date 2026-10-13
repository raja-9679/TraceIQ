import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { Loader2, Check, X } from "lucide-react";
import { AuthLayout } from "@/components/redesign-local/groupF/AuthLayout";
import { authPrimary } from "@/components/redesign-local/groupF/authStyles";

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
        <AuthLayout>
            <div className="flex flex-col items-center gap-3 py-2 text-center" role="status" aria-live="polite">
                {state === "loading" && (
                    <>
                        <Loader2 className="h-6 w-6 animate-spin text-ink-soft" aria-hidden="true" />
                        <p className="text-sm text-ink-soft">Verifying your email…</p>
                    </>
                )}
                {state === "ok" && (
                    <>
                        <span className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-pastel-mint text-pastel-ink-mint" aria-hidden="true">
                            <Check className="h-5 w-5" strokeWidth={2.4} />
                        </span>
                        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Email verified</h1>
                        <p className="text-sm text-ink-soft">Your email address has been confirmed.</p>
                    </>
                )}
                {state === "error" && (
                    <>
                        <span className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-pastel-peach text-pastel-ink-peach" aria-hidden="true">
                            <X className="h-5 w-5" strokeWidth={2.4} />
                        </span>
                        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink">Verification failed</h1>
                        <p className="text-sm text-ink-soft">This link is invalid or has expired.</p>
                    </>
                )}
            </div>
            <Link to="/" className={authPrimary}>Go to dashboard</Link>
        </AuthLayout>
    );
}
