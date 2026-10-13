import { useState } from "react";
import { Link, useSearchParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import { AuthError, AuthField, AuthLayout } from "@/components/redesign-local/groupF/AuthLayout";
import { authInput, authLink, authPrimary } from "@/components/redesign-local/groupF/authStyles";

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
        <AuthLayout
            title="Choose a new password"
            subtitle={token ? "Use at least 8 characters." : undefined}
            footer={<Link to="/login" className={authLink}>Back to sign in</Link>}
        >
            {!token ? (
                <AuthError>Missing reset token. Use the link from your email.</AuthError>
            ) : (
                <form onSubmit={submit} className="flex flex-col gap-4">
                    <AuthField id="reset-password" label="New password">
                        <input
                            id="reset-password"
                            type="password"
                            required
                            autoFocus
                            autoComplete="new-password"
                            value={password}
                            onChange={(e) => setPassword(e.target.value)}
                            className={authInput}
                        />
                    </AuthField>
                    <AuthField id="reset-confirm" label="Confirm password">
                        <input
                            id="reset-confirm"
                            type="password"
                            required
                            autoComplete="new-password"
                            value={confirm}
                            onChange={(e) => setConfirm(e.target.value)}
                            className={authInput}
                        />
                    </AuthField>
                    <button type="submit" disabled={submitting} className={authPrimary}>
                        {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                        Update password
                    </button>
                </form>
            )}
        </AuthLayout>
    );
}
