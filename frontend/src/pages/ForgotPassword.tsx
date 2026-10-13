import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { Loader2 } from "lucide-react";
import { AuthField, AuthLayout } from "@/components/redesign-local/groupF/AuthLayout";
import { authInput, authLink, authPrimary } from "@/components/redesign-local/groupF/authStyles";

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
        <AuthLayout
            title="Reset your password"
            subtitle={done ? undefined : "We'll email you a link to choose a new one."}
            footer={<Link to="/login" className={authLink}>Back to sign in</Link>}
        >
            {done ? (
                <p role="status" className="text-sm leading-relaxed text-ink-soft">
                    If an account exists for that email, we've sent a reset link. Check your inbox.
                </p>
            ) : (
                <form onSubmit={submit} className="flex flex-col gap-4">
                    <AuthField id="forgot-email" label="Work email">
                        <input
                            id="forgot-email"
                            type="email"
                            required
                            autoFocus
                            autoComplete="email"
                            value={email}
                            onChange={(e) => setEmail(e.target.value)}
                            className={authInput}
                            placeholder="name@company.com"
                        />
                    </AuthField>
                    <button type="submit" disabled={submitting} className={authPrimary}>
                        {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                        Send reset link
                    </button>
                </form>
            )}
        </AuthLayout>
    );
}
