import { useState, useMemo, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Loader2, AlertCircle } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthShell } from "@/components/auth/AuthShell";
import { PasswordInput } from "@/components/auth/PasswordInput";
import { useRegistrationOpen } from "@/hooks/use-registration-open";
import { useDemoEnabled } from "@/hooks/use-demo-enabled";
import {
  loginSchema,
  fieldErrorsFrom,
  translateAuthError,
  translateDemoError,
  type LoginValues,
  type FieldErrors,
} from "@/lib/validation";
import { readSubmitUrl, type SubmitUrlState } from "@/lib/submit-url";

type LoginErrors = FieldErrors<LoginValues>;

export function LoginPage() {
  const [values, setValues] = useState<LoginValues>({ email: "", password: "" });
  const [touched, setTouched] = useState<Partial<Record<keyof LoginValues, boolean>>>({});
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(false);
  const [demoLoading, setDemoLoading] = useState(false);

  const login = useAuthStore((s) => s.login);
  const loginDemo = useAuthStore((s) => s.loginDemo);
  const navigate = useNavigate();
  const location = useLocation();
  const registrationOpen = useRegistrationOpen();
  const demoEnabled = useDemoEnabled();

  const errors: LoginErrors = useMemo(() => {
    const result = loginSchema.safeParse(values);
    return result.success ? {} : fieldErrorsFrom(result.error);
  }, [values]);

  const handleBlur = (field: keyof LoginValues) => () =>
    setTouched((t) => ({ ...t, [field]: true }));
  const handleChange = (field: keyof LoginValues) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setValues((v) => ({ ...v, [field]: e.target.value }));

  const canSubmit = Object.keys(errors).length === 0 && !loading && !demoLoading;

  /** Opens the board, or hands a URL pasted on the landing page to /generate. */
  const navigateAfterLogin = (): void => {
    const submitUrl = readSubmitUrl(location.state);
    if (!submitUrl) {
      navigate("/board");
      return;
    }
    navigate("/generate", { state: { submitUrl } satisfies SubmitUrlState });
  };

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setTouched({ email: true, password: true });
    if (Object.keys(errors).length > 0) return;
    setSubmitError("");
    setLoading(true);
    try {
      await login(values.email, values.password);
      navigateAfterLogin();
    } catch (err) {
      setSubmitError(translateAuthError(err, "login"));
    } finally {
      setLoading(false);
    }
  };

  const handleDemoLogin = async (): Promise<void> => {
    setSubmitError("");
    setDemoLoading(true);
    try {
      await loginDemo();
      navigateAfterLogin();
    } catch (err) {
      setSubmitError(translateDemoError(err));
    } finally {
      setDemoLoading(false);
    }
  };

  return (
    <AuthShell
      variant="signin"
      title="Welcome back"
      subtitle="Sign in to keep building your video knowledge base."
      footer={
        registrationOpen ? (
          <>
            Don&apos;t have an account?{" "}
            <Link to="/register" className="font-medium text-primary hover:underline">
              Sign up
            </Link>
          </>
        ) : null
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {submitError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{submitError}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            value={values.email}
            onChange={handleChange("email")}
            onBlur={handleBlur("email")}
            placeholder="you@example.com"
            aria-invalid={touched.email && !!errors.email}
            aria-describedby={touched.email && errors.email ? "email-error" : undefined}
            autoComplete="email"
            required
          />
          {touched.email && errors.email && (
            <p id="email-error" className="text-xs text-destructive">
              {errors.email}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <PasswordInput
            id="password"
            value={values.password}
            onChange={handleChange("password")}
            onBlur={handleBlur("password")}
            placeholder="Enter your password"
            aria-invalid={touched.password && !!errors.password}
            aria-describedby={touched.password && errors.password ? "password-error" : undefined}
            autoComplete="current-password"
            required
          />
          {touched.password && errors.password && (
            <p id="password-error" className="text-xs text-destructive">
              {errors.password}
            </p>
          )}
        </div>

        <Button type="submit" className="w-full" disabled={!canSubmit}>
          {loading && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
          {loading ? "Signing in..." : "Sign in"}
        </Button>

        {demoEnabled && (
          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={handleDemoLogin}
            disabled={loading || demoLoading}
          >
            {demoLoading && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
            {demoLoading ? "Starting the demo..." : "Try the demo"}
          </Button>
        )}
      </form>
    </AuthShell>
  );
}
