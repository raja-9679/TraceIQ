import { useEffect, useState } from "react";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { QRCodeSVG } from "qrcode.react";
import { Loader2, ShieldCheck, Copy, Check } from "lucide-react";

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
    onComplete: (verify: VerifyResponse) => void;
}

/** TOTP enrollment flow: QR → code check → recovery codes. Shared by the
 *  signup "secure your account" step and the MFA_REQUIRED login gate. */
export default function MfaEnrollment({ token, onSkip, onComplete }: Props) {
    const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
    const [code, setCode] = useState("");
    const [verifyResult, setVerifyResult] = useState<VerifyResponse | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [copied, setCopied] = useState(false);

    const headers = { Authorization: `Bearer ${token}` };

    useEffect(() => {
        axios.post(`${API_BASE_URL}/auth/mfa/setup`, {}, { headers })
            .then((r) => setSetup(r.data))
            .catch((err) => setError(err.response?.data?.detail || "Could not start 2FA enrollment."));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [token]);

    const verify = async () => {
        setBusy(true);
        setError("");
        try {
            const r = await axios.post(`${API_BASE_URL}/auth/mfa/verify`, { code }, { headers });
            setVerifyResult(r.data);
        } catch (err: any) {
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
                <div className="flex items-center gap-2 text-emerald-700">
                    <ShieldCheck className="w-5 h-5" />
                    <span className="text-sm font-semibold">Two-factor authentication is on</span>
                </div>
                <p className="text-xs text-zinc-500">
                    Save these recovery codes somewhere safe — each works once if you lose your
                    authenticator. <span className="font-semibold">They are shown only now.</span>
                </p>
                <div className="grid grid-cols-2 gap-1.5 p-3 bg-zinc-50 border border-zinc-200 rounded-lg font-mono text-xs text-zinc-700">
                    {verifyResult.recovery_codes.map((c) => <span key={c}>{c}</span>)}
                </div>
                <div className="flex items-center gap-2">
                    <button type="button" onClick={copyCodes}
                        className="flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-zinc-600 bg-white border border-zinc-200 rounded-lg hover:bg-zinc-50 transition-colors">
                        {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
                        {copied ? "Copied" : "Copy codes"}
                    </button>
                    <button type="button" onClick={() => onComplete(verifyResult)}
                        className="flex-1 px-3 py-2 text-xs font-semibold text-white bg-zinc-900 rounded-lg hover:bg-zinc-800 transition-colors">
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
                <h3 className="text-sm font-bold text-zinc-900">Set up two-factor authentication</h3>
                <p className="text-xs text-zinc-500 mt-1">
                    Scan the QR code with an authenticator app (Google Authenticator, Authy,
                    1Password…), then enter the 6-digit code it shows.
                </p>
            </div>
            {setup ? (
                <div className="flex items-start gap-4">
                    <div className="p-2.5 bg-white border border-zinc-200 rounded-xl shadow-sm shrink-0">
                        <QRCodeSVG value={setup.otpauth_uri} size={132} level="M" />
                    </div>
                    <div className="space-y-2 min-w-0">
                        <p className="text-[10px] font-semibold text-zinc-500 uppercase tracking-widest">Manual entry key</p>
                        <code className="block text-[11px] font-mono text-zinc-700 bg-zinc-50 border border-zinc-200 rounded-md px-2 py-1.5 break-all">
                            {setup.secret}
                        </code>
                        <input
                            value={code}
                            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                            placeholder="123456"
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            className="w-full px-3 py-2.5 rounded-lg bg-white border border-zinc-200 text-zinc-900 text-sm font-mono tracking-[0.3em] placeholder:tracking-normal placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-zinc-900/5 focus:border-zinc-400 transition-all shadow-sm"
                        />
                    </div>
                </div>
            ) : !error ? (
                <div className="flex items-center gap-2 text-zinc-400 text-sm py-6 justify-center">
                    <Loader2 className="w-4 h-4 animate-spin" /> Preparing enrollment…
                </div>
            ) : null}
            {error && <p className="text-xs text-rose-600">{error}</p>}
            <div className="flex items-center gap-2">
                {onSkip && (
                    <button type="button" onClick={onSkip}
                        className="px-3 py-2 text-xs font-semibold text-zinc-500 hover:text-zinc-700 transition-colors">
                        Skip for now
                    </button>
                )}
                <button type="button" onClick={verify} disabled={code.length !== 6 || busy || !setup}
                    className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-xs font-semibold text-white bg-zinc-900 rounded-lg hover:bg-zinc-800 disabled:opacity-40 transition-colors">
                    {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                    Verify & enable
                </button>
            </div>
        </div>
    );
}
