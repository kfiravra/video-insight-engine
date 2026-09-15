import { useState, useMemo, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, AlertCircle } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthShell } from "@/components/auth/AuthShell";
import { PasswordInput } from "@/components/auth/PasswordInput";
import { useRegistrationOpen } from "@/hooks/use-registration-open";
import {
  loginSchema,
  fieldErrorsFrom,
  translateAuthError,
  type LoginValues,
  type FieldErrors,
} from "@/lib/validation";

type LoginErrors = FieldErrors<LoginValues>;

export function LoginPage() {
  const [values, setValues] = useState<LoginValues>({ email: "", password: "" });
  const [touched, setTouched] = useState<Partial<Record<keyof LoginValues, boolean>>>({});
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(false);

  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();
  const registrationOpen = useRegistrationOpen();

  const errors: LoginErrors = useMemo(() => {
    const result = loginSchema.safeParse(values);
    return result.success ? {} : fieldErrorsFrom(result.error);
  }, [values]);

  const handleBlur = (field: keyof LoginValues) => () =>
    setTouched((t) => ({ ...t, [field]: true }));
  const handleChange = (field: keyof LoginValues) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setValues((v) => ({ ...v, [field]: e.target.value }));

  const canSubmit = Object.keys(errors).length === 0 && !loading;

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setTouched({ email: true, password: true });
    if (Object.keys(errors).length > 0) return;
    setSubmitError("");
    setLoading(true);
    try {
      await login(values.email, values.password);
      navigate("/board");
    } catch (err) {
      setSubmitError(translateAuthError(err, "login"));
    } finally {
      setLoading(false);
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
      </form>
    </AuthShell>
  );
}
