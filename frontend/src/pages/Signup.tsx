import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import axios from "axios";
import { API_BASE_URL } from "@/lib/api";
import { Loader2, ArrowRight } from "lucide-react";
import MfaEnrollment from "@/components/MfaEnrollment";
import { AuthError, AuthField, AuthLayout } from "@/components/redesign-local/groupF/AuthLayout";
import { authInput, authLink, authPrimary } from "@/components/redesign-local/groupF/authStyles";
import { cn } from "@/lib/utils";

export default function Signup() {
    const { login } = useAuth();
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const inviteToken = searchParams.get("token");
    const inviteEmail = searchParams.get("email");

    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState("");
    // Post-registration 2FA step. Optional by default ("secure your account");
    // mandatory (no skip) when the instance enforces MFA_REQUIRED, in which
    // case mfaStep.token is the enrollment challenge and verify returns the
    // session tokens.
    const [mfaStep, setMfaStep] = useState<{
        token: string; mandatory: boolean; user: any;
    } | null>(null);

    const { register, handleSubmit, watch, formState: { errors } } = useForm({
        defaultValues: {
            email: inviteEmail || "",
            fullName: "",
            password: "",
            confirmPassword: "",
            orgName: "",
            projectName: ""
        }
    });
    const password = watch("password");

    const onSubmit = async (data: any) => {
        setIsLoading(true);
        setError("");
        try {
            // Register
            const payload: any = {
                email: data.email,
                password: data.password,
                full_name: data.fullName,
                organization_name: data.orgName,
                project_name: data.projectName
            };

            if (inviteToken) {
                payload.invite_token = inviteToken;
            }

            const registerResponse = await axios.post(`${API_BASE_URL}/auth/register`, payload);

            // Login automatically after registration
            const formData = new FormData();
            formData.append('username', data.email);
            formData.append('password', data.password);

            const loginResponse = await axios.post(`${API_BASE_URL}/auth/login`, formData);

            if (loginResponse.data.mfa_setup_required) {
                // Instance enforces MFA: no session yet, enrollment is mandatory.
                setMfaStep({
                    token: loginResponse.data.mfa_token,
                    mandatory: true,
                    user: registerResponse.data,
                });
                return;
            }

            const { access_token, refresh_token } = loginResponse.data;
            login(access_token, registerResponse.data, refresh_token);
            // Offer (optional) 2FA enrollment before entering the app.
            setMfaStep({ token: access_token, mandatory: false, user: registerResponse.data });
        } catch (err: any) {
            console.error("Registration failed", err);
            setError(err.response?.data?.detail || "Registration failed. Please try again.");
        } finally {
            setIsLoading(false);
        }
    };

    const describe = (id: string, has: boolean) => (has ? `${id}-error` : undefined);

    return (
        <AuthLayout
            title={mfaStep ? undefined : "Create your account"}
            subtitle={mfaStep ? undefined : inviteToken ? "You're joining a workspace by invitation." : "Start recording and running browser tests."}
            footer={
                <>
                    Already have an account?{" "}
                    <Link to="/login" className={authLink}>Sign in</Link>
                </>
            }
        >
            {mfaStep ? (
                <MfaEnrollment
                    token={mfaStep.token}
                    onSkip={mfaStep.mandatory ? undefined : () => navigate("/")}
                    onCancel={mfaStep.mandatory ? () => {
                        setMfaStep(null);
                        navigate("/login");
                    } : undefined}
                    onExpired={() => {
                        setMfaStep(null);
                        navigate("/login", {
                            state: { message: "Your enrollment session expired — please sign in again." },
                        });
                    }}
                    onComplete={(verify) => {
                        if (mfaStep.mandatory) {
                            if (verify.access_token) {
                                // Enrollment unblocked the session — store it now.
                                login(verify.access_token, mfaStep.user, verify.refresh_token);
                                navigate("/");
                            } else {
                                // No session issued — can't enter the app; send to login.
                                navigate("/login", {
                                    state: { message: "Two-factor authentication is enabled. Please sign in." },
                                });
                            }
                        } else {
                            navigate("/");
                        }
                    }}
                />
            ) : (
                <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-4">
                    {error && <AuthError>{error}</AuthError>}

                    <AuthField id="signup-name" label="Full name" error={errors.fullName?.message as string | undefined}>
                        <input
                            id="signup-name"
                            {...register("fullName", { required: "Required" })}
                            autoComplete="name"
                            aria-invalid={!!errors.fullName}
                            aria-describedby={describe('signup-name', !!errors.fullName)}
                            className={authInput}
                            placeholder="Name"
                        />
                    </AuthField>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <AuthField id="signup-org" label="Organisation (optional)">
                            <input
                                id="signup-org"
                                {...register("orgName")}
                                autoComplete="organization"
                                className={authInput}
                                placeholder="My Company"
                            />
                        </AuthField>
                        <AuthField id="signup-project" label="Project (optional)">
                            <input
                                id="signup-project"
                                {...register("projectName")}
                                autoComplete="off"
                                className={authInput}
                                placeholder="Alpha"
                            />
                        </AuthField>
                    </div>

                    <AuthField id="signup-email" label="Work email" error={errors.email?.message as string | undefined}>
                        <input
                            id="signup-email"
                            {...register("email", {
                                required: "Required",
                                pattern: { value: /^\S+@\S+$/i, message: "Invalid email" }
                            })}
                            type="email"
                            autoComplete="email"
                            aria-invalid={!!errors.email}
                            aria-describedby={describe('signup-email', !!errors.email)}
                            className={authInput}
                            placeholder="name@company.com"
                        />
                    </AuthField>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <AuthField id="signup-password" label="Password" error={errors.password?.message as string | undefined}>
                            <input
                                id="signup-password"
                                {...register("password", {
                                    required: "Required",
                                    minLength: { value: 6, message: "Min 6 chars" }
                                })}
                                type="password"
                                autoComplete="new-password"
                                aria-invalid={!!errors.password}
                                aria-describedby={describe('signup-password', !!errors.password)}
                                className={authInput}
                            />
                        </AuthField>
                        <AuthField id="signup-confirm" label="Confirm password" error={errors.confirmPassword?.message as string | undefined}>
                            <input
                                id="signup-confirm"
                                {...register("confirmPassword", {
                                    required: "Required",
                                    validate: (val: string) => val === password || "Mismatch"
                                })}
                                type="password"
                                autoComplete="new-password"
                                aria-invalid={!!errors.confirmPassword}
                                aria-describedby={describe('signup-confirm', !!errors.confirmPassword)}
                                className={authInput}
                            />
                        </AuthField>
                    </div>

                    <button type="submit" disabled={isLoading} className={cn(authPrimary, 'mt-1')}>
                        {isLoading ? <Loader2 className="h-4 w-4 animate-spin" aria-label="Creating account" /> : (
                            <>
                                Create account <ArrowRight className="h-4 w-4" aria-hidden="true" />
                            </>
                        )}
                    </button>
                </form>
            )}
        </AuthLayout>
    );
}
