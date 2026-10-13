import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { QRCodeSVG } from "qrcode.react";
import { Loader2, ShieldCheck, Copy, Check } from "lucide-react";
import { Pill } from "@/components/redesign/primitives";

interface VerifyResponse {
    mfa_enabled: boolean;
    recovery_codes: string[];
    // Present when enrollment was forced by the MFA_REQUIRED policy — the
    // session tokens login withheld.
    access_token?: string;
    refresh_token?: string;
}

interface Props {
    /** Bearer used for /mfa/setup + /mfa/verify: a normal access token
     *  (optional post-signup enrollment) or the mfa_setup_pending challenge
     *  (MFA_REQUIRED policy). */
    token: string;
    /** Optional enrollment shows a skip action; policy-forced does not. */
    onSkip?: () => void;
    /** Fired when the challenge token is rejected (401) by setup/verify — the
     *  enrollment session has expired and the caller should return to sign-in. */
    onExpired?: () => void;
    /** Explicit "Back to sign in" control; when provided a cancel button shows. */
    onCancel?: () => void;
    onComplete: (verify: VerifyResponse) => void;
}

/** TOTP enrollment flow: QR → code check → recovery codes. Shared by the
 *  signup "secure your account" step and the MFA_REQUIRED login gate. */
export default function MfaEnrollment({ token, onSkip, onExpired, onCancel, onComplete }: Props) {
    const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
    const [code, setCode] = useState("");
    const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);

    const headers = { Authorization: `Bearer ${token}` };

    useEffect(() => {
        // A `cancelled` guard so StrictMode's double-invoke (or a token change)
        // ignores the stale /mfa/setup response. The backend stores a fresh
        // secret per call, so honoring a stale response would render a QR that
        // no longer matches the stored secret.
        let cancelled = false;
        setSetup(null);
        axios.post(`${API_BASE_URL}/auth/mfa/setup`, {}, { headers })
            .then((r) => { if (!cancelled) setSetup(r.data); })
            .catch((err) => {
                if (cancelled) return;
                if (err.response?.status === 401) { onExpired?.(); return; }
                setError(err.response?.data?.detail || "Could not start 2FA enrollment.");
            });
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [token]);

    const verify = async () => {
        setBusy(true);
        setError("");
        try {
            const r = await axios.post(`${API_BASE_URL}/auth/mfa/verify`, { code }, { headers });
            setVerifyResult(r.data);
        } catch (err: any) {
            if (err.response?.status === 401) { onExpired?.(); return; }
            setError(err.response?.data?.detail || "Invalid authentication code.");
        } finally {
            setBusy(false);
        }
    };

    const copyCodes = () => {
        navigator.clipboard.writeText((verifyResult?.recovery_codes || []).join("\n"));
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    // Step 3: recovery codes (shown exactly once)
    if (verifyResult) {
        return (
            <div className="space-y-4">
                <div className="flex items-center gap-2">
                    <Pill tone="mint">
                        <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                        On
                    </Pill>
                    <span className="text-sm font-semibold text-ink">Two-factor authentication is on</span>
                </div>
                <p className="text-[13px] leading-relaxed text-ink-soft">
                    Save these recovery codes somewhere safe — each works once if you lose your
                    authenticator. <span className="font-semibold text-ink">They are shown only now.</span>
                </p>
                <div className="grid grid-cols-2 gap-1.5 rounded-2xl border border-border bg-background p-3 font-mono text-xs text-ink">
                    {verifyResult.recovery_codes.map((c) => <span key={c}>{c}</span>)}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <button type="button" onClick={copyCodes}
                        className="flex min-h-10 items-center gap-1.5 rounded-full border border-border bg-white px-4 text-sm font-medium text-ink transition-colors hover:bg-background">
                        {copied ? <Check className="h-4 w-4" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
                        {copied ? "Copied" : "Copy codes"}
                    </button>
                    <button type="button" onClick={() => onComplete(verifyResult)}
                        className="min-h-10 flex-1 rounded-full bg-ink px-4 text-sm font-medium text-white transition-colors hover:bg-ink/85">
                        I've saved them — continue
                    </button>
                </div>
            </div>
        );
    }

    // Steps 1+2: QR + code confirmation
    return (
        <div className="space-y-4">
            <div>
                <h3 className="text-base font-semibold text-ink">Set up two-factor authentication</h3>
                <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">
                    Scan the QR code with an authenticator app (Google Authenticator, Authy,
                    1Password…), then enter the 6-digit code it shows.
                </p>
            </div>
            {setup ? (
                <div className="flex flex-col items-start gap-4 sm:flex-row">
                    <div className="shrink-0 rounded-2xl border border-border bg-white p-2.5">
                        <QRCodeSVG value={setup.otpauth_uri} size={132} level="M" />
                    </div>
                    <div className="w-full min-w-0 space-y-3">
                        <div className="space-y-1">
                            <p className="text-xs font-medium text-ink-soft">Can't scan? Enter this key manually</p>
                            <code className="block break-all rounded-xl border border-border bg-background px-2.5 py-1.5 font-mono text-xs text-ink">
                                {setup.secret}
                            </code>
                        </div>
                        <div className="space-y-1">
                            <label htmlFor="mfa-enroll-code" className="block text-sm font-medium text-ink">6-digit code</label>
                            <input
                                id="mfa-enroll-code"
                                value={code}
                                onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                                placeholder="123456"
                                inputMode="numeric"
                                autoComplete="one-time-code"
                                className="h-11 w-full rounded-full border border-input bg-white px-4 font-mono text-sm tracking-[0.3em] text-ink placeholder:tracking-normal placeholder:text-ink-muted focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                            />
                        </div>
                    </div>
                </div>
            ) : !error ? (
                <div className="flex items-center justify-center gap-2 py-6 text-sm text-ink-soft" role="status">
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Preparing enrollment…
                </div>
            ) : null}
            {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
            <div className="flex flex-wrap items-center gap-2">
                {onSkip && (
                    <button type="button" onClick={onSkip}
                        className="min-h-10 rounded-full border border-border bg-white px-4 text-sm font-medium text-ink-soft transition-colors hover:bg-background hover:text-ink">
                        Skip for now
                    </button>
                )}
                {onCancel && (
                    <button type="button" onClick={onCancel}
                        className="min-h-10 rounded-full border border-border bg-white px-4 text-sm font-medium text-ink-soft transition-colors hover:bg-background hover:text-ink">
                        Back to sign in
                    </button>
                )}
                <button type="button" onClick={verify} disabled={code.length !== 6 || busy || !setup}
                    className="flex min-h-10 flex-1 items-center justify-center gap-2 rounded-full bg-ink px-4 text-sm font-medium text-white transition-colors hover:bg-ink/85 disabled:opacity-40">
                    {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                    Verify & enable
                </button>
            </div>
        </div>
    );
}
