import { useState, useMemo, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, AlertCircle, Check } from "lucide-react";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AuthShell } from "@/components/auth/AuthShell";
import { PasswordInput } from "@/components/auth/PasswordInput";
import { useRegistrationOpen } from "@/hooks/use-registration-open";
import { cn } from "@/lib/utils";
import {
  registerSchema,
  fieldErrorsFrom,
  translateAuthError,
  type RegisterValues,
  type FieldErrors,
} from "@/lib/validation";

interface PasswordChecks {
  length: boolean;
  upper: boolean;
  lower: boolean;
  digit: boolean;
}

interface PasswordStrength {
  score: number;
  label: string;
  tone: "weak" | "ok" | "strong";
}

function passwordChecks(pw: string): PasswordChecks {
  return {
    length: pw.length >= 8,
    upper: /[A-Z]/.test(pw),
    lower: /[a-z]/.test(pw),
    digit: /\d/.test(pw),
  };
}

function passwordStrength(checks: PasswordChecks): PasswordStrength {
  const score = Object.values(checks).filter(Boolean).length;
  if (score <= 2) return { score, label: "Weak", tone: "weak" };
  if (score === 3) return { score, label: "Getting there", tone: "ok" };
  return { score, label: "Strong", tone: "strong" };
}

type RegisterErrors = FieldErrors<RegisterValues>;

export function RegisterPage() {
  const [values, setValues] = useState<RegisterValues>({ name: "", email: "", password: "" });
  const [touched, setTouched] = useState<Partial<Record<keyof RegisterValues, boolean>>>({});
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(false);

  const register = useAuthStore((s) => s.register);
  const navigate = useNavigate();
  const registrationOpen = useRegistrationOpen();

  const errors: RegisterErrors = useMemo(() => {
    const result = registerSchema.safeParse(values);
    return result.success ? {} : fieldErrorsFrom(result.error);
  }, [values]);

  const checks = useMemo(() => passwordChecks(values.password), [values.password]);
  const strength = useMemo(() => passwordStrength(checks), [checks]);

  const handleBlur = (field: keyof RegisterValues) => () =>
    setTouched((t) => ({ ...t, [field]: true }));
  const handleChange = (field: keyof RegisterValues) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      setValues((v) => ({ ...v, [field]: e.target.value }));

  const canSubmit = Object.keys(errors).length === 0 && !loading;

  const handleSubmit = async (e: FormEvent): Promise<void> => {
    e.preventDefault();
    setTouched({ name: true, email: true, password: true });
    if (Object.keys(errors).length > 0) return;
    setSubmitError("");
    setLoading(true);
    try {
      await register(values.email, values.password, values.name.trim());
      navigate("/board");
    } catch (err) {
      setSubmitError(translateAuthError(err, "register"));
    } finally {
      setLoading(false);
    }
  };

  if (registrationOpen === false) {
    return (
      <AuthShell
        variant="signup"
        title="Registration is closed"
        subtitle="This deployment is invite-only. Ask the owner for an account."
      >
        <Button asChild className="w-full">
          <Link to="/login">Sign in</Link>
        </Button>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      variant="signup"
      title="Create your account"
      subtitle="Turn every video you watch into an interactive study guide."
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="font-medium text-primary hover:underline">
            Sign in
          </Link>
        </>
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
          <Label htmlFor="name">Name</Label>
          <Input
            id="name"
            type="text"
            value={values.name}
            onChange={handleChange("name")}
            onBlur={handleBlur("name")}
            placeholder="John Doe"
            aria-invalid={touched.name && !!errors.name}
            autoComplete="name"
            required
          />
          {touched.name && errors.name && (
            <p className="text-xs text-destructive">{errors.name}</p>
          )}
        </div>

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
            autoComplete="email"
            required
          />
          {touched.email && errors.email && (
            <p className="text-xs text-destructive">{errors.email}</p>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <PasswordInput
            id="password"
            value={values.password}
            onChange={handleChange("password")}
            onBlur={handleBlur("password")}
            placeholder="Create a password"
            aria-invalid={touched.password && !!errors.password}
            aria-describedby="password-strength password-requirements"
            autoComplete="new-password"
            required
          />

          {/* Strength meter — bars fill progressively as each requirement is met. */}
          {values.password && (
            <div id="password-strength" className="space-y-1">
              <div
                className="flex gap-1"
                role="progressbar"
                aria-valuenow={strength.score}
                aria-valuemin={0}
                aria-valuemax={4}
                aria-label={`Password strength: ${strength.label}`}
              >
                {[0, 1, 2, 3].map((i) => {
                  const filled = i < strength.score;
                  return (
                    <div
                      key={i}
                      className={cn(
                        "h-1 flex-1 rounded-full origin-left transition-[background-color,transform] ease-out",
                        filled
                          ? strength.tone === "strong"
                            ? "bg-success"
                            : strength.tone === "ok"
                              ? "bg-warning"
                              : "bg-destructive/70"
                          : "bg-muted",
                      )}
                      style={{
                        transform: filled ? "scaleX(1)" : "scaleX(0.15)",
                        transitionDuration: "320ms",
                        transitionDelay: filled ? `${i * 60}ms` : "0ms",
                      }}
                    />
                  );
                })}
              </div>
              <p
                key={strength.tone}
                className={cn(
                  "text-xs font-medium transition-colors",
                  strength.tone === "strong" && "text-success",
                  strength.tone === "ok" && "text-warning",
                  strength.tone === "weak" && "text-destructive",
                )}
              >
                {strength.label}
              </p>
            </div>
          )}

          {/* Requirements — inline chip row preserves the single-column form
              rhythm while staying compact on one line at this width. */}
          <ul
            id="password-requirements"
            className="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-xs text-muted-foreground"
          >
            <Requirement met={checks.length} label="8+ characters" />
            <Requirement met={checks.upper} label="Uppercase" />
            <Requirement met={checks.lower} label="Lowercase" />
            <Requirement met={checks.digit} label="Number" />
          </ul>
        </div>

        <Button type="submit" className="w-full" disabled={!canSubmit}>
          {loading && <Loader2 className="me-2 h-4 w-4 animate-spin" />}
          {loading ? "Creating..." : "Create account"}
        </Button>
      </form>
    </AuthShell>
  );
}

interface RequirementProps {
  met: boolean;
  label: string;
}

function Requirement({ met, label }: RequirementProps) {
  return (
    <li className={cn("flex items-center gap-1.5 transition-colors", met && "text-success")}>
      <Check
        key={met ? "met" : "unmet"}
        className={cn(
          "h-3 w-3 shrink-0 transition-opacity",
          met ? "opacity-100 scale-pulse-once" : "opacity-30",
        )}
        aria-hidden="true"
      />
      <span>{label}</span>
    </li>
  );
}
